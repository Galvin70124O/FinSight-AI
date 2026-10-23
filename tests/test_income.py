import pandas as pd

from utils import build_dashboard_payload

ML_STUB = {
    "expense_prediction": type("Exp", (), {"next_month": "N/A", "predicted_expense": 0.0, "confidence": 0.0})(),
    "health_score": type("Health", (), {"score": 0, "label": "-"})(),
    "anomaly_count": 0,
}

ROWS = [
    {"Date": "2024-12-01", "Category": "Salary", "Amount": 80000.0, "Type": "Income", "Description": "Salary"},
    {"Date": "2024-12-05", "Category": "Rent", "Amount": 20000.0, "Type": "Expense", "Description": "Rent"},
    {"Date": "2025-01-01", "Category": "Salary", "Amount": 80000.0, "Type": "Income", "Description": "Salary"},
    {"Date": "2025-01-15", "Category": "Freelance", "Amount": 10000.0, "Type": "Income", "Description": "Project"},
    {"Date": "2025-01-05", "Category": "Rent", "Amount": 20000.0, "Type": "Expense", "Description": "Rent"},
    {"Date": "2025-01-09", "Category": "Food", "Amount": 5000.0, "Type": "Expense", "Description": "Groceries"},
]


def payload(app, rows=ROWS):
    with app.app_context():
        return build_dashboard_payload(pd.DataFrame(rows), ml_results=ML_STUB)


def test_income_totals_balance_and_savings_rate(app):
    metrics = payload(app)["metrics"]

    assert metrics["total_income"] == 170000.0
    assert metrics["total_expense"] == 45000.0
    assert metrics["current_balance"] == 125000.0
    assert metrics["savings_rate"] == 73.5


def test_income_vs_expense_per_month(app):
    monthly = payload(app)["summary"]["income_vs_expense"]

    assert monthly["labels"] == ["Dec 2024", "Jan 2025"]
    assert monthly["income"] == [80000.0, 90000.0]
    assert monthly["expense"] == [20000.0, 25000.0]


def test_income_only_month_still_appears(app):
    rows = ROWS + [{"Date": "2025-02-01", "Category": "Salary", "Amount": 80000.0, "Type": "Income", "Description": "Salary"}]
    monthly = payload(app, rows)["summary"]["income_vs_expense"]

    assert monthly["labels"][-1] == "Feb 2025"
    assert monthly["expense"][-1] == 0.0


def test_recent_transactions_include_income(app):
    types = {tx["Type"] for tx in payload(app)["recent_transactions"]}
    assert types == {"Income", "Expense"}
