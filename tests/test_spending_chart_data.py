import pandas as pd

from models import save_transactions
from utils import build_dashboard_payload

ML_STUB = {
    "expense_prediction": type("Exp", (), {"next_month": "Apr", "predicted_expense": 100.0, "confidence": 0.8})(),
    "health_score": type("Health", (), {"score": 80, "label": "Good"})(),
    "anomaly_count": 0,
}


def test_save_transactions_skips_duplicate_rows(db, user_id):
    tx_df = pd.DataFrame([
        {"Date": "2024-01-05", "Category": "Dining", "Amount": 120.0, "Type": "Expense", "Description": "Lunch"},
        {"Date": "2024-01-05", "Category": "Dining", "Amount": 120.0, "Type": "Expense", "Description": "Lunch"},
        {"Date": "2024-02-01", "Category": "Groceries", "Amount": 80.0, "Type": "Expense", "Description": "Groceries"},
        {"Date": "2024-02-01", "Category": "Groceries", "Amount": 80.0, "Type": "Expense", "Description": "Groceries"},
        {"Date": "2024-03-15", "Category": "Transport", "Amount": 45.0, "Type": "Expense", "Description": "Train"},
    ])

    assert save_transactions(db, user_id, tx_df, source_file="upload.csv") == 3
    # Importing the same file again adds nothing.
    assert save_transactions(db, user_id, tx_df, source_file="upload.csv") == 0
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,)).fetchone()[0] == 3


def test_spending_chart_aggregates_expenses_by_month(app):
    with app.app_context():
        payload = build_dashboard_payload(pd.DataFrame([
            {"Date": "2024-01-05", "Category": "Dining", "Amount": 120.0, "Type": "Expense", "Description": "Lunch"},
            {"Date": "2024-01-20", "Category": "Dining", "Amount": 30.0, "Type": "Expense", "Description": "Snack"},
            {"Date": "2024-02-01", "Category": "Groceries", "Amount": 80.0, "Type": "Expense", "Description": "Groceries"},
            {"Date": "2024-03-15", "Category": "Transport", "Amount": 45.0, "Type": "Expense", "Description": "Train"},
        ]), ml_results=ML_STUB)

    monthly = payload["summary"]["chart_series"]["monthly"]
    totals = dict(zip(monthly["labels"], monthly["values"]))
    assert totals["Jan 2024"] == 150.0
    assert totals["Feb 2024"] == 80.0
    assert totals["Mar 2024"] == 45.0
