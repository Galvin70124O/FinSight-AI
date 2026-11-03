import pandas as pd

from utils import build_dashboard_payload


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
        return build_dashboard_payload(pd.DataFrame(rows))


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


def test_overspending_is_described_in_rupees_not_a_negative_share(app):
    rows = ROWS + [{"Date": "2025-01-20", "Category": "Education", "Amount": 300000.0, "Type": "Expense", "Description": "Fees"}]
    insight = payload(app, rows)["insight"]

    assert "₹1,75,000 more than you earned" in insight
    assert "keeping" not in insight and "-" not in insight


def test_counts_of_one_are_singular(app):
    from utils import plural

    one_month = [row for row in ROWS if row["Date"].startswith("2025-01")]
    assert payload(app, one_month)["insight"].startswith("Over 1 month you earned")
    assert plural(1, "transaction") == "1 transaction"
    assert plural(0, "transaction") == "0 transactions"


def test_categories_past_the_seventh_fold_into_one_row(app):
    names = ["Rent", "Groceries", "Dining", "Transport", "Shopping", "Bills", "Health", "Travel", "Gifts", "Books"]
    rows = ROWS[:1] + [
        {"Date": "2025-01-10", "Category": name, "Amount": 1000.0 * (10 - i), "Type": "Expense", "Description": name}
        for i, name in enumerate(names)
    ]
    data = payload(app, rows)
    cats = data["summary"]["category_distribution"]

    assert len(cats) == 8
    assert cats[-1] == {"Category": "Everything else", "Total": 6000.0}  # Travel + Gifts + Books
    assert sum(c["Total"] for c in cats) == data["metrics"]["total_expense"]


def test_eight_categories_are_listed_without_a_remainder_row(app):
    rows = ROWS[:1] + [
        {"Date": "2025-01-10", "Category": f"C{i}", "Amount": 100.0 + i, "Type": "Expense", "Description": "x"} for i in range(8)
    ]
    assert "Everything else" not in [c["Category"] for c in payload(app, rows)["summary"]["category_distribution"]]
