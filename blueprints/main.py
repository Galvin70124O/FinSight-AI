from __future__ import annotations

import logging

import pandas as pd
from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.utils import secure_filename

from database import get_db
from ml.pipeline import run_ml_pipeline
from models import get_user_transactions, save_transactions
from utils import CSVValidationError, allowed_file, build_dashboard_payload, clean_transaction_dataframe, read_uploaded_csv, validate_uploaded_csv

main_bp = Blueprint("main", __name__)
logger = logging.getLogger(__name__)


@main_bp.before_request
def require_login():
    """Protect application routes from unauthenticated access."""
    allowed_routes = {"auth.login", "auth.register", "static", "main.index"}
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


@main_bp.route("/dashboard")
def dashboard():
    """Render the main dashboard for authenticated users."""
    db = get_db()
    transactions = get_user_transactions(db, session["user_id"])
    df = _transactions_frame(transactions)
    ml_results = run_ml_pipeline(df, current_app.config["TRAINED_MODELS_DIR"]) if not df.empty else None
    payload = build_dashboard_payload(df, ml_results=ml_results)

    return render_template("dashboard.html", payload=payload, transactions=transactions)


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
            saved_rows = save_transactions(get_db(), session["user_id"], cleaned_df, source_file=filename)
        except CSVValidationError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("main.upload"))
        except Exception:
            logger.exception("CSV import failed for user %s", session["user_id"])
            flash("Import failed. Please check that the file matches the CSV format and try again.", "danger")
            return redirect(url_for("main.upload"))

        flash(f"Imported {saved_rows} transaction(s) successfully.", "success")

        return redirect(url_for("main.dashboard"))

    return render_template("upload.html")
