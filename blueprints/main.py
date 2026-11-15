from __future__ import annotations

import csv
import io
import logging
import os
import re
import secrets
import zlib
from collections import Counter

import pandas as pd
from flask import Blueprint, abort, current_app, flash, get_flashed_messages, get_template_attribute, jsonify, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from database import get_db
from ml import statement_parser
from ml.categorise import CATEGORIES, UNCATEGORISED, categorise, looks_like_person
from ml.pipeline import INSIGHTS_VERSION, compute_insights
from ml.statement_parser import StatementError
from models import (
    claim_import_token, get_cached_insights, get_category_overrides, get_column_mapping, get_user_categories, get_user_transactions,
    clear_insights, imported_signatures, merchant_rows, recategorise, restore_categories, save_category_overrides, save_column_mapping,
    save_insights, save_transactions, transaction_signature,
)
from utils import (
    COLUMNS, REQUIRED_COLUMNS, CSVValidationError, plural, build_dashboard_payload, clean_transaction_dataframe,
    read_uploaded_csv, shift_to_recent_months, validate_uploaded_csv,
)

main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

REVIEW_MAX_AGE = 3600  # seconds a review form stays valid
MERCHANT_ROWS_PAGE = 20  # transactions shown per merchant on the Categories page, then "Show more"
ROWS_SHOWN_PER_MERCHANT = 10  # enough to recognise a merchant; keeps a 2 MB file's review page light
TOO_MANY_ROWS = (
    "This file has too many rows to review in one go. Split it into smaller files (for example one per "
    "quarter, each with the header row) and upload them one at a time. Rows you have already imported are skipped."
)
COLUMNS_TOO_BIG = (
    "This file is too big to match its columns in one go. Upload one month of it first, with the header row: "
    "FinSight remembers the columns you pick, and the whole file then imports without this step."
)


@main_bp.before_request
def require_login():
    """Protect application routes from unauthenticated access."""
    user_id = session.get("user_id")
    # Before the public pages too: "/" shows the overview to a signed-in session.
    if user_id is not None and get_db().execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        session.clear()  # the account was deleted on another device; its rows are gone
        flash("This account was deleted, so you were signed out.", "info")
        return redirect(url_for("auth.login"))
    allowed_routes = {"auth.login", "auth.register", "static", "main.index", "main.privacy", "main.terms"}
    if request.endpoint in allowed_routes:
        return None
    if user_id is None:
        return redirect(url_for("auth.login"))
    return None


@main_bp.route("/")
def index():
    """Render the landing experience for guests and redirect authenticated users to the dashboard."""
    if "user_id" in session:
        return dashboard()
    return render_template("landing.html")


@main_bp.route("/privacy")
def privacy():
    return render_template("privacy.html")


@main_bp.route("/terms")
def terms():
    return render_template("terms.html")


def _transactions_frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Id": row["id"],  # for editing one row; the insights only read the named columns
                "Date": row["date"],
                "Category": row["category"],
                "Amount": row["amount"],
                "Type": row["type"],
                "Description": row["description"],
                "Merchant": row["merchant_key"],  # stored at import; find_recurring() reads it
            }
            for row in rows
        ]
    )


def refresh_insights(db, user_id: int) -> dict:
    """Recompute and cache one user's insights. Call whenever their transactions change."""
    insights = compute_insights(_transactions_frame(get_user_transactions(db, user_id)))
    save_insights(db, user_id, insights)
    return insights


@main_bp.route("/dashboard")
def dashboard():
    """Render the main dashboard for authenticated users."""
    db = get_db()
    transactions = get_user_transactions(db, session["user_id"])
    df = _transactions_frame(transactions)
    # Insights are computed on import and cached per user; recompute only if
    # they are missing or were saved in an older format.
    insights = get_cached_insights(db, session["user_id"])
    if insights is None or insights.get("version") != INSIGHTS_VERSION:
        insights = refresh_insights(db, session["user_id"])
    payload = build_dashboard_payload(df, insights)
    own_categories = _own_categories(db, session["user_id"])

    return render_template("dashboard.html", payload=payload, transactions=transactions, categories=CATEGORIES, own_categories=own_categories)


@main_bp.route("/demo-data", methods=["POST"])
def load_demo_data():
    """Import the bundled sample transactions into the current user's account only."""
    demo_path = os.path.join(current_app.config["DATASET_DIR"], "sample_transactions.csv")
    # The sample covers the 8 full months before today, so it always looks current.
    cleaned_df = clean_transaction_dataframe(shift_to_recent_months(pd.read_csv(demo_path)))
    db = get_db()
    saved_rows = save_transactions(db, session["user_id"], cleaned_df, source_file="demo_data")
    refresh_insights(db, session["user_id"])
    flash(f"Loaded {plural(saved_rows, 'sample transaction')}.", "success")
    return redirect(url_for("main.dashboard"))


@main_bp.route("/upload", methods=["GET", "POST"])
def upload():
    """Handle CSV upload and derive analytics from the uploaded data."""
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file was selected.", "danger")
            return redirect(url_for("main.upload"))

        file = request.files["file"]
        if file.filename == "":
            flash("No file was selected.", "danger")
            return redirect(url_for("main.upload"))

        # The file is parsed in memory and never written to disk; only its
        # sanitised name is kept as a label on the imported rows.
        filename = secure_filename(file.filename) or "upload.csv"
        data = file.read()  # at most MAX_CONTENT_LENGTH (2 MB); Flask refuses anything bigger
        try:
            db = get_db()
            own_columns, for_reader = _finsight_format(file.filename, data)
            if {column.lower() for column in REQUIRED_COLUMNS} <= own_columns and not for_reader:  # our own format: its original path
                return _import_finsight_csv(db, data, filename)
            # A bank statement. The file's own name is used to tell its type and in messages about it.
            grid = statement_parser.read_grid(data, file.filename)
            try:
                layout = statement_parser.locate(grid)
            except StatementError:
                if len(own_columns) >= 2:  # our format with columns missing (Date,Amount): name them, as before
                    return _import_finsight_csv(db, data, filename)
                raise
            if not any(layout.rows):  # column names and nothing below: no choice of columns could help
                raise StatementError(statement_parser.NO_TRANSACTIONS.format(row=layout.header_index + 1))
            saved = get_column_mapping(db, session["user_id"], layout.key)  # this user's own, for this header row
            fitted = saved and statement_parser.fit_roles(saved[0], layout.width)
            roles = fitted or statement_parser.guess_roles(layout)
            result = statement_parser.convert(layout, roles, saved[1] if fitted else None)
            if not result.ok:  # the column screen, with the saved choice preselected when it still fits
                return _columns(layout, roles, result, filename)
            flash(_read_message(layout, result), "info")
            return _import(db, session["user_id"], clean_transaction_dataframe(result.frame), filename)
        except (CSVValidationError, StatementError) as exc:
            flash(str(exc), "danger")
            return redirect(url_for("main.upload"))
        except Exception:
            logger.exception("CSV import failed for user %s", session["user_id"])
            flash("Import failed. Check that the file is a bank statement or a CSV with the columns listed below, and try again.", "danger")
            return redirect(url_for("main.upload"))

    return render_template("upload.html")


def _finsight_format(name: str, data: bytes) -> tuple[set[str], bool]:
    """FinSight's own column names (date, category, amount, type, description) in the first row of a CSV, and
    whether the statement reader should read it anyway: a Type that says Dr/Cr like a bank's, or a date that is
    not YYYY-MM-DD. The old path would drop those rows, or read 05/10/2026 as 10 May; the reader knows the words
    and reads dates day first, or asks when the file doesn't say which."""
    if not name.lower().endswith(".csv"):
        return set(), False
    rows = csv.reader(io.StringIO(data.decode("utf-8-sig", "replace")))
    own: set[str] = set()
    try:
        header = [cell.strip().lower() for cell in next(rows, [])]
        own = set(header) & {column.lower() for column in COLUMNS}
        at = {column: header.index(column) for column in ("date", "type") if column in own}

        def cell(row, column):
            return row[at[column]].strip() if column in at and at[column] < len(row) else ""

        return own, any(
            statement_parser.says_dr_or_cr(cell(row, "type"))
            or (cell(row, "date") and not statement_parser.ISO_DATE.match(cell(row, "date")))  # a blank date stays as before
            for row in rows
        )
    except csv.Error:  # a field over csv's size limit: pandas reads our own format as before; others get a message
        return own, False


def _import_finsight_csv(db, data: bytes, filename: str):
    df = read_uploaded_csv(io.BytesIO(data))
    validate_uploaded_csv(df)
    return _import(db, session["user_id"], clean_transaction_dataframe(df), filename)


def _columns_serializer() -> URLSafeTimedSerializer:
    # As on the review page: the file's rows wait in the form, signed so they can't be altered, never on disk.
    return URLSafeTimedSerializer(current_app.secret_key, salt="import-columns")


def _columns(layout, roles: list[str], result, filename: str, date_order: str | None = None):
    """Ask which column is which, showing what stopped the reader. map_columns() reads the answer."""
    payload = _columns_serializer().dumps({"u": session["user_id"], "f": filename, "h": layout.header_index,
                                           "g": [layout.header, *layout.rows]})
    # The form posts the rows back with one short field per column; keep it under the request limit.
    if len(payload) + 64 * layout.width > current_app.config["MAX_CONTENT_LENGTH"] - 4096:
        flash(COLUMNS_TOO_BIG, "danger")
        return redirect(url_for("main.upload"))
    columns = []
    for index, (label, role) in enumerate(zip(layout.labels, roles)):
        cells = (row[index].strip() for row in layout.rows if index < len(row))
        named = index < len(layout.header) and layout.header[index]
        columns.append({"index": index, "label": label if named else label[:1].upper() + label[1:],  # "Column E" on its own
                        "role": role, "samples": [cell for cell in cells if cell][:3]})
    return render_template(
        "import_columns.html", columns=columns, payload=payload, filename=filename, header_row=layout.header_index + 1,
        errors=result.errors, more=result.error_count - len(result.errors), date_example=result.date_example,
        date_order=date_order, fix_rows=any(error.startswith("Row ") for error in result.errors),
    )


@main_bp.route("/upload/columns", methods=["POST"])
def map_columns():
    """Read the statement again with the columns picked on the column screen, and remember them once it reads."""
    user_id = session["user_id"]
    try:
        data = _columns_serializer().loads(request.form.get("payload", ""), max_age=REVIEW_MAX_AGE)
    except BadSignature:  # also covers an expired form
        data = None
    if not data or data.get("u") != user_id:
        flash("This page has expired or could not be read, so nothing was imported. Please upload the file again.", "danger")
        return redirect(url_for("main.upload"))

    layout = statement_parser.Layout(data["h"], data["g"][0], data["g"][1:])
    roles = [request.form.get(f"role-{i}", "") for i in range(layout.width)]  # convert() checks every value
    date_order = request.form.get("date_order") if request.form.get("date_order") in ("day", "month") else None
    result = statement_parser.convert(layout, roles, date_order)
    if not result.ok:
        return _columns(layout, roles, result, data["f"], date_order)
    db = get_db()
    save_column_mapping(db, user_id, layout.key, roles, date_order)  # only a choice that reads the file
    flash(_read_message(layout, result), "info")
    try:
        return _import(db, user_id, clean_transaction_dataframe(result.frame), data["f"])
    except CSVValidationError as exc:
        flash(str(exc), "danger")
    except Exception:
        logger.exception("Statement import failed for user %s", user_id)
        flash("Import failed. Please check the file and try again.", "danger")
    return redirect(url_for("main.upload"))


def _read_message(layout, result) -> str:
    """'Read 6 transactions from rows 10–17 (HDFC Bank layout). 1 row with no debit or credit was skipped.'"""
    message = (f"Read {plural(len(result.frame), 'transaction')} from rows {result.first_row}–{result.last_row} "
               f"({layout.preset_label} layout).")
    if result.skipped:
        message += f" {plural(result.skipped, 'row')} with no debit or credit {'was' if result.skipped == 1 else 'were'} skipped."
    return message


def _import(db, user_id: int, df: pd.DataFrame, filename: str):
    """Show cleaned rows on the review page when some need a category, or save them straight away."""
    if (df["Category"] == "").any():
        return _review(db, user_id, df, filename)
    saved_rows = save_transactions(db, user_id, df, source_file=filename)
    refresh_insights(db, user_id)
    flash(f"Imported {plural(saved_rows, 'transaction')}.", "success")
    return redirect(url_for("main.dashboard"))


def _review_serializer() -> URLSafeTimedSerializer:
    # Signed (and zlib-compressed when that is shorter) with the app's secret key, so the rows
    # can wait in the review form instead of on disk, and cannot be altered on the way back.
    return URLSafeTimedSerializer(current_app.secret_key, salt="import-review")


def _own_categories(db, user_id: int) -> list[str]:
    """Up to 20 of the user's own categories that are not in the fixed list, offered after it."""
    return [category for category in get_user_categories(db, user_id) if category not in CATEGORIES]


def _allowed_categories(db, user_id: int) -> set[str]:
    """What a row or merchant may be changed to: the fixed list and every category the user already has
    (a select always offers a row's own category, even one outside the 20 listed)."""
    rows = db.execute("SELECT DISTINCT category FROM transactions WHERE user_id = ?", (user_id,)).fetchall()
    return (set(CATEGORIES) | {row["category"] for row in rows}) - {UNCATEGORISED}


_ANCHOR = re.compile(r"m[0-9a-f]{1,8}")


UNDO_MAX_AGE = 600  # seconds an Undo stays possible


def _wants_json() -> bool:
    """The page script asks for JSON; a plain form post (no JavaScript) gets the redirect."""
    return request.accept_mimetypes.best == "application/json"


def _undo_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="category-undo")


def _changed(anchor: str, message: str, category: str, undo: dict, back: str):
    """The answer to a successful change: JSON with an Undo token for the page script, else a redirect."""
    if _wants_json():
        icon = get_template_attribute("_macros.html", "category_icon")(category)
        return jsonify(ok=True, message=message, category=category, icon=str(icon), undo=_undo_serializer().dumps(undo))
    _flash_at(anchor, message, "success")
    return redirect(back)


def _refused(anchor: str | None, message: str, back: str, status: int = 400):
    if _wants_json():
        return jsonify(ok=False, error=message), status
    _flash_at(anchor, message, "danger")
    return redirect(back)


def _flash_at(anchor: str | None, message: str, kind: str) -> None:
    """A message shown beside the merchant or section the page scrolls back to, not at the top out of view."""
    flash(message, f"at-{anchor} {kind}" if anchor else kind)


def _anchor(key: str) -> str:
    """A stable HTML id for a merchant; keys contain spaces."""
    return f"m{zlib.crc32(key.encode()):x}"


def _merchants(db, user_id: int) -> list[dict]:
    """The user's merchants, from the stored merchant_key, with counts, totals and categories (no rows).
    Merchants with any Uncategorized row come first."""
    merchants: dict[str, dict] = {}
    for row in db.execute(
        "SELECT merchant_key, category, COUNT(*) AS n, SUM(amount) AS total FROM transactions WHERE user_id = ? GROUP BY merchant_key, category",
        (user_id,),
    ).fetchall():
        key = row["merchant_key"] or ""
        merchant = merchants.setdefault(key, {"key": key, "anchor": _anchor(key), "categories": Counter(), "total": 0.0, "count": 0, "rows": []})
        merchant["categories"][row["category"]] += int(row["n"])
        merchant["count"] += int(row["n"])
        merchant["total"] += float(row["total"])
    # One example description per merchant: its latest saved row.
    for row in db.execute(
        "SELECT merchant_key, description FROM transactions WHERE id IN (SELECT MAX(id) FROM transactions WHERE user_id = ? GROUP BY merchant_key)",
        (user_id,),
    ).fetchall():
        merchants[row["merchant_key"] or ""]["example"] = row["description"]
    for merchant in merchants.values():
        merchant["category"] = merchant["categories"].most_common(1)[0][0]  # the "current" category: the most common
        merchant["uncategorised"] = merchant["categories"][UNCATEGORISED]
    return sorted(merchants.values(), key=lambda m: (m["uncategorised"] == 0, -m["uncategorised"], -m["total"], m["key"]))


def _categories_url(values, anchor: str | None = None) -> str:
    """The Categories page with its search, open merchants and "Show more" kept. Only known shapes pass."""
    query = values.get("q", "").strip()[:100]
    opened = [a for a in values.getlist("open") if a == "none" or _ANCHOR.fullmatch(a)]
    more = values.get("more", "") if _ANCHOR.fullmatch(values.get("more", "")) else None
    rows = values.get("rows", type=int)
    return url_for(
        "main.categories", q=query or None, open=opened or None, more=more, rows=rows if more and rows else None,
        _anchor=anchor if anchor and _ANCHOR.fullmatch(anchor) else None,
    )


@main_bp.route("/categories", methods=["GET", "POST"])
def categories():
    """Every merchant with its current category; change a whole merchant (remembered) or single rows."""
    user_id, db = session["user_id"], get_db()
    if request.method == "POST":
        key = request.form.get("merchant", "")
        rows = merchant_rows(db, user_id, key) if key else []  # this user's own rows, never ids from the form
        anchor = _anchor(key) if rows else None
        back = _categories_url(request.form, anchor)
        category = request.form.get("category", "").strip()
        remember = bool(request.form.get("remember"))  # ticked by default for businesses, unticked for people
        if not rows:
            return _refused(None, "That merchant is not in your transactions. Nothing was changed.", back, 404)
        if category not in _allowed_categories(db, user_id):
            return _refused(anchor, "Choose a category from the list. Nothing was changed.", back)
        previous: dict[str, list[int]] = {}
        for row in rows:
            previous.setdefault(row["category"], []).append(row["id"])
        previous_override = get_category_overrides(db, user_id).get(key)
        changed = recategorise(db, user_id, [row["id"] for row in rows], category, remember_as=key if remember else "")
        clear_insights(db, user_id)
        note = "Remembered for future imports." if remember else "Not remembered for future imports."
        undo = {"u": user_id, "rows": previous, "merchant": key if remember else "", "override": previous_override, "anchor": anchor, "back": back}
        return _changed(anchor, f"{key}: {plural(changed, 'transaction')} changed to {category}. {note}", category, undo, back)

    merchants = _merchants(db, user_id)

    query = request.args.get("q", "").strip()[:100]
    needing = [m["anchor"] for m in merchants if m["uncategorised"]]
    # First visit: a few merchants with Uncategorized rows open, ready to fix. "open=none" means "all closed".
    opened = [a for a in request.args.getlist("open") if a == "none" or _ANCHOR.fullmatch(a)]
    if not opened:
        opened = needing if len(needing) <= 5 else ["none"]
    more = request.args.get("more", "")
    rows = max(MERCHANT_ROWS_PAGE, request.args.get("rows", MERCHANT_ROWS_PAGE, type=int))
    needle = query.lower()
    # A merchant just changed may no longer match the search ("uncategorized"); keep it, with its message, in view.
    just_changed = {category.split(" ")[0][3:] for category, _ in get_flashed_messages(with_categories=True) if category.startswith("at-")}
    shown = [
        m for m in merchants
        if not needle or m["anchor"] in just_changed
        or needle in m["key"].lower() or needle in m["category"].lower() or needle in m["example"].lower()
    ]
    for merchant in shown:
        merchant["person"] = looks_like_person(merchant["key"], [merchant["example"]])
        merchant["open"] = merchant["anchor"] in opened
        merchant["limit"] = rows if merchant["anchor"] == more else MERCHANT_ROWS_PAGE
        if merchant["open"]:  # only open merchants fetch their rows
            merchant["rows"] = merchant_rows(db, user_id, merchant["key"], merchant["limit"])
    return render_template(
        "categories.html", merchants=shown, merchant_count=len(merchants), needing_count=len(needing), query=query,
        opened=[a for a in opened if a != "none"], categories=CATEGORIES, own_categories=_own_categories(db, user_id),
        more=more if _ANCHOR.fullmatch(more) else "", rows=rows,
    )


@main_bp.route("/transactions/<int:transaction_id>/category", methods=["POST"])
def change_transaction_category(transaction_id: int):
    """Change one of the user's transactions. Never remembered for its merchant."""
    user_id, db = session["user_id"], get_db()
    if request.form.get("return_to") == "categories":
        anchor = request.form.get("anchor", "")
        anchor = anchor if _ANCHOR.fullmatch(anchor) else None
        back = _categories_url(request.form, anchor)
    else:
        anchor, back = "transactions", url_for("main.dashboard", _anchor="transactions")
    category = request.form.get("category", "").strip()
    if category not in _allowed_categories(db, user_id):
        return _refused(anchor, "Choose a category from the list. Nothing was changed.", back)
    row = db.execute("SELECT category FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id)).fetchone()
    if row is None:  # not one of this user's transactions
        if _wants_json():
            return jsonify(ok=False, error="That transaction is not in your account any more. Reload the page."), 404
        abort(404)
    recategorise(db, user_id, [transaction_id], category)
    clear_insights(db, user_id)
    undo = {"u": user_id, "rows": {row["category"]: [transaction_id]}, "merchant": "", "override": None, "anchor": anchor, "back": back}
    return _changed(anchor, f"One transaction changed to {category}.", category, undo, back)


@main_bp.route("/categories/undo", methods=["POST"])
def undo_category_change():
    """Put back what one change replaced: each row's earlier category and the merchant's earlier correction."""
    user_id, db = session["user_id"], get_db()
    try:
        data = _undo_serializer().loads(request.form.get("undo", ""), max_age=UNDO_MAX_AGE)
    except BadSignature:  # also covers an expired Undo
        data = None
    if not data or data.get("u") != user_id:
        return _refused(None, "That change can no longer be undone. Change it back by hand.", url_for("main.categories"))
    restore_categories(db, user_id, {category: [int(i) for i in ids] for category, ids in data["rows"].items()}, data["merchant"], data["override"])
    clear_insights(db, user_id)
    if _wants_json():
        _flash_at(data["anchor"], "Undone. The earlier categories are back.", "success")  # shown after the page reloads
        return jsonify(ok=True)
    return _changed(data["anchor"], "Undone. The earlier categories are back.", "", data, data["back"])


def _review(db, user_id: int, df: pd.DataFrame, filename: str):
    """Suggest categories for the rows that have none and show them, grouped by merchant, before saving."""
    imported, in_file = imported_signatures(db, user_id), set()
    rows = []  # [date, amount, type, description, category from the file or "", merchant group or -1]
    skipped = 0
    for row in df.itertuples(index=False):
        date = row.Date.date().isoformat()
        signature = transaction_signature(date, row.Amount, row.Type, row.Description)
        skipped += signature in imported
        if signature not in imported and signature not in in_file:  # a repeat within the file is saved once, as before
            in_file.add(signature)
            rows.append([date, float(row.Amount), row.Type, row.Description, row.Category, -1])
    if not rows:
        flash(f"Nothing new to import: all {plural(skipped, 'row')} in this file were imported before.", "info")
        return redirect(url_for("main.upload"))

    blank = [row for row in rows if not row[4]]
    if not blank:  # the rows without a category were all imported before
        saved_rows = save_transactions(db, user_id, df, source_file=filename)
        refresh_insights(db, user_id)
        flash(f"Imported {plural(saved_rows, 'transaction')}.", "success")
        return redirect(url_for("main.dashboard"))
    keys, suggestions = categorise([row[3] for row in blank], get_category_overrides(db, user_id))
    counts = Counter(keys)
    # Rows that need a category first, then the model's least sure guesses, then rules and corrections.
    order = sorted(suggestions, key=lambda key: (
        suggestions[key].category is not None,
        suggestions[key].confidence if suggestions[key].category and suggestions[key].source == "model" else 2,
        -counts[key],
        key,
    ))
    index = {key: i for i, key in enumerate(order)}
    for row, key in zip(blank, keys):
        row[5] = index[key]

    token = secrets.token_urlsafe(16)
    payload = _review_serializer().dumps({
        "u": user_id, "t": token, "f": filename,
        "g": [[key, suggestions[key].category] for key in order],
        "r": rows,
    })
    # The form posts the payload plus one short field per merchant; keep it under the request limit.
    if len(payload) + 64 * len(order) > current_app.config["MAX_CONTENT_LENGTH"] - 4096:
        flash(TOO_MANY_ROWS, "danger")
        return redirect(url_for("main.upload"))

    rows_by_key: dict[str, list] = {}
    for row, key in zip(blank, keys):
        rows_by_key.setdefault(key, []).append(row)
    groups = []
    for key in order:
        group_rows = sorted(rows_by_key[key], key=lambda row: row[0], reverse=True)
        groups.append({
            "index": index[key],
            "name": key,
            "suggestion": suggestions[key],
            "count": len(group_rows),
            "total": sum(row[1] for row in group_rows),
            "rows": group_rows[:ROWS_SHOWN_PER_MERCHANT],
        })
    return render_template(
        "import_review.html",
        groups=groups,
        payload=payload,
        filename=filename,
        categories=CATEGORIES,
        own_categories=_own_categories(db, user_id),
        row_count=len(rows),
        own_category_count=len(rows) - len(blank),
        skipped=skipped,
        needs_count=sum(1 for key in order if suggestions[key].category is None),
    )


@main_bp.route("/upload/save", methods=["POST"])
def save_review():
    """Save reviewed rows once, and remember every merchant whose category the user changed."""
    user_id = session["user_id"]
    try:
        data = _review_serializer().loads(request.form.get("payload", ""), max_age=REVIEW_MAX_AGE)
    except BadSignature:  # also covers an expired form
        data = None
    if not data or data.get("u") != user_id:
        flash("This review has expired or could not be read, so nothing was saved. Please upload the file again.", "danger")
        return redirect(url_for("main.upload"))

    db = get_db()
    choices = set(CATEGORIES) | set(_own_categories(db, user_id))
    chosen = [request.form.get(f"group-{i}", "").strip() for i in range(len(data["g"]))]
    if any(value and value not in choices for value in chosen):
        flash("A chosen category is not on the list, so nothing was saved. Please upload the file again.", "danger")
        return redirect(url_for("main.upload"))

    if not claim_import_token(db, user_id, data["t"]):
        flash("These rows were already saved, so nothing was imported twice.", "info")
        return redirect(url_for("main.dashboard"))

    frame = pd.DataFrame(
        [
            {"Date": date, "Category": category or (chosen[group] if group >= 0 else "") or UNCATEGORISED,
             "Amount": amount, "Type": kind, "Description": description}
            for date, amount, kind, description, category, group in data["r"]
        ],
        columns=["Date", "Category", "Amount", "Type", "Description"],
    )
    try:
        saved_rows = save_transactions(db, user_id, frame, source_file=data["f"])
        # Only merchants the user changed and asked to remember (people are unticked by default).
        corrections = {
            key: value for i, ((key, suggested), value) in enumerate(zip(data["g"], chosen))
            if key and value and value != suggested and request.form.get(f"remember-{i}")
        }
        if corrections:
            save_category_overrides(db, user_id, corrections)
        refresh_insights(db, user_id)
    except Exception:
        logger.exception("Saving a reviewed import failed for user %s", user_id)
        flash("Saving did not finish. Upload the file again; any rows that were saved are skipped.", "danger")
        return redirect(url_for("main.upload"))

    message = f"Imported {plural(saved_rows, 'transaction')}."
    left = int((frame["Category"] == UNCATEGORISED).sum())
    if left:
        message += f" {plural(left, 'row')} saved as {UNCATEGORISED}."
    flash(message, "success")
    return redirect(url_for("main.dashboard"))
