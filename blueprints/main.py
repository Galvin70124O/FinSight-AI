from __future__ import annotations

import logging
import os
import secrets
from collections import Counter

import pandas as pd
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.utils import secure_filename

from database import get_db
from ml.categorise import CATEGORIES, UNCATEGORISED, categorise
from ml.pipeline import INSIGHTS_VERSION, compute_insights
from models import (
    claim_import_token, get_cached_insights, get_category_overrides, get_user_categories, get_user_transactions,
    imported_signatures, save_category_overrides, save_insights, save_transactions, transaction_signature,
)
from utils import CSVValidationError, plural, allowed_file, build_dashboard_payload, clean_transaction_dataframe, read_uploaded_csv, shift_to_recent_months, validate_uploaded_csv

main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)

REVIEW_MAX_AGE = 3600  # seconds a review form stays valid
ROWS_SHOWN_PER_MERCHANT = 10  # enough to recognise a merchant; keeps a 2 MB file's review page light
TOO_MANY_ROWS = (
    "This file has too many rows to review in one go. Split it into smaller CSV files (for example one per "
    "quarter, each with the header row) and upload them one at a time. Rows you have already imported are skipped."
)


@main_bp.before_request
def require_login():
    """Protect application routes from unauthenticated access."""
    allowed_routes = {"auth.login", "auth.register", "static", "main.index", "main.privacy", "main.terms"}
    if request.endpoint in allowed_routes:
        return None
    if "user_id" not in session:
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
                "Date": row["date"],
                "Category": row["category"],
                "Amount": row["amount"],
                "Type": row["type"],
                "Description": row["description"],
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

    return render_template("dashboard.html", payload=payload, transactions=transactions)


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

        if not allowed_file(file.filename):
            flash("Only CSV files are supported.", "danger")
            return redirect(url_for("main.upload"))

        # The file is parsed in memory and never written to disk; only its
        # sanitised name is kept as a label on the imported rows.
        filename = secure_filename(file.filename) or "upload.csv"
        try:
            df = read_uploaded_csv(file.stream)
            validate_uploaded_csv(df)
            cleaned_df = clean_transaction_dataframe(df)
            db = get_db()
            if (cleaned_df["Category"] == "").any():
                return _review(db, session["user_id"], cleaned_df, filename)
            saved_rows = save_transactions(db, session["user_id"], cleaned_df, source_file=filename)
            refresh_insights(db, session["user_id"])
        except CSVValidationError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("main.upload"))
        except Exception:
            logger.exception("CSV import failed for user %s", session["user_id"])
            flash("Import failed. Please check that the file matches the CSV format and try again.", "danger")
            return redirect(url_for("main.upload"))

        flash(f"Imported {plural(saved_rows, 'transaction')}.", "success")

        return redirect(url_for("main.dashboard"))

    return render_template("upload.html")


def _review_serializer() -> URLSafeTimedSerializer:
    # Signed (and zlib-compressed when that is shorter) with the app's secret key, so the rows
    # can wait in the review form instead of on disk, and cannot be altered on the way back.
    return URLSafeTimedSerializer(current_app.secret_key, salt="import-review")


def _own_categories(db, user_id: int) -> list[str]:
    """Up to 20 of the user's own categories that are not in the fixed list, offered after it."""
    return [category for category in get_user_categories(db, user_id) if category not in CATEGORIES]


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
