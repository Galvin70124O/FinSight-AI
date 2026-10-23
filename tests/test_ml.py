import json

import pandas as pd

from ml.anomaly_detection import detect_anomalies
from ml.expense_prediction import forecast_expenses
from ml.health_score import NO_INCOME_MESSAGE, calculate_health_score
from ml.pipeline import compute_insights


def tx(date, category, amount, kind="Expense", description="x"):
    return {"Date": pd.Timestamp(date), "Category": category, "Amount": float(amount), "Type": kind, "Description": description}


def monthly_spend(months, amounts):
    return pd.DataFrame([tx(f"{month}-10", "Food", amount) for month, amount in zip(months, amounts)])


# --- forecast ---------------------------------------------------------------

def test_forecast_needs_three_months():
    result = forecast_expenses(monthly_spend(["2025-01", "2025-02"], [1000, 1200]))

    assert result.enough_data is False
    assert result.predicted is None
    assert "at least 3 months" in result.message


def test_forecast_with_three_to_five_months_is_low_confidence():
    result = forecast_expenses(monthly_spend(["2024-11", "2024-12", "2025-01", "2025-02"], [1000, 1150, 1050, 1200]))

    assert result.enough_data is True
    assert result.low_confidence is True
    assert result.month == "Mar 2025"
    assert result.low <= result.predicted <= result.high
    assert result.high > result.low


def test_forecast_with_six_months_is_normal_confidence():
    months = ["2024-09", "2024-10", "2024-11", "2024-12", "2025-01", "2025-02"]
    result = forecast_expenses(monthly_spend(months, [1000, 1100, 1200, 1300, 1400, 1500]))

    assert result.low_confidence is False
    assert round(result.predicted) == 1600  # perfectly linear history
    assert round(result.low) == round(result.high) == 1600  # no scatter, no uncertainty


def test_forecast_range_widens_with_erratic_spending():
    months = ["2024-09", "2024-10", "2024-11", "2024-12", "2025-01", "2025-02"]
    steady = forecast_expenses(monthly_spend(months, [1000, 1050, 980, 1020, 1010, 990]))
    erratic = forecast_expenses(monthly_spend(months, [400, 1800, 600, 1500, 300, 1400]))

    assert (erratic.high - erratic.low) > 5 * (steady.high - steady.low)


def test_months_without_spending_count_as_zero():
    df = pd.DataFrame([tx("2025-01-05", "Food", 900), tx("2025-02-01", "Salary", 5000, "Income"), tx("2025-03-05", "Food", 900)])
    assert forecast_expenses(df).months_used == 3


# --- anomalies --------------------------------------------------------------

def test_anomaly_is_relative_to_the_category():
    rows = [tx(f"2025-01-{d:02d}", "Dining", a) for d, a in zip(range(1, 7), [600, 650, 700, 620, 680, 5000])]
    rows += [tx(f"2025-01-{d:02d}", "Rent", 22000) for d in range(1, 5)]

    flagged = detect_anomalies(pd.DataFrame(rows))

    # The 5,000 dinner is flagged even though rent is far larger in raw terms.
    assert [(f["category"], f["amount"]) for f in flagged] == [("Dining", 5000.0)]
    assert flagged[0]["typical"] == 665.0


def test_anomaly_with_zero_mad_uses_fifty_percent_rule():
    rows = [tx(f"2025-0{m}-01", "Rent", 22000) for m in range(1, 7)]
    rows.append(tx("2025-07-01", "Rent", 30000))  # +36%: not flagged
    rows.append(tx("2025-08-01", "Rent", 44000))  # +100%: flagged
    rows.append(tx("2025-09-01", "Rent", 8000))   # -64%: lower, never flagged

    flagged = detect_anomalies(pd.DataFrame(rows))

    assert [f["amount"] for f in flagged] == [44000.0]


def test_lower_than_usual_amounts_are_not_flagged():
    amounts = [600, 650, 700, 620, 680, 640, 20]
    rows = [tx(f"2025-01-{d:02d}", "Dining", a) for d, a in zip(range(1, 8), amounts)]

    assert detect_anomalies(pd.DataFrame(rows)) == []


def test_anomaly_skips_small_categories():
    rows = [tx("2025-01-01", "Travel", 500), tx("2025-01-02", "Travel", 60000), tx("2025-01-03", "Travel", 450)]
    assert detect_anomalies(pd.DataFrame(rows)) == []


def test_income_is_never_flagged():
    rows = [tx(f"2025-0{m}-01", "Salary", 80000, "Income") for m in range(1, 6)] + [tx("2025-06-01", "Salary", 400000, "Income")]
    assert detect_anomalies(pd.DataFrame(rows)) == []


# --- health score -----------------------------------------------------------

def test_health_score_without_income_is_not_shown():
    health = calculate_health_score(pd.DataFrame([tx("2025-01-05", "Food", 900), tx("2025-02-05", "Food", 900)]))

    assert health.score is None
    assert health.message == NO_INCOME_MESSAGE == "Add income transactions to see your health score."


def test_health_score_breakdown_adds_up():
    rows = []
    for month in ["2025-01", "2025-02", "2025-03"]:
        rows += [tx(f"{month}-01", "Salary", 100000, "Income"), tx(f"{month}-05", "Rent", 30000)]

    health = calculate_health_score(pd.DataFrame(rows))

    assert [c["name"] for c in health.components] == ["Savings rate", "Months covered", "Steady spending"]
    assert health.score == 100.0  # 70% saved, every month covered, identical spending
    assert health.label == "Healthy"


def test_health_score_penalises_overspending():
    rows = [tx("2025-01-01", "Salary", 50000, "Income"), tx("2025-01-05", "Shopping", 60000),
            tx("2025-02-01", "Salary", 50000, "Income"), tx("2025-02-05", "Shopping", 60000)]
    health = calculate_health_score(pd.DataFrame(rows))

    assert health.components[0]["points"] == 0
    assert health.components[1]["points"] == 0
    assert health.label == "Needs attention"


def test_insights_are_plain_json_data():
    insights = compute_insights(monthly_spend(["2025-01", "2025-02", "2025-03"], [1000, 1100, 1200]))
    assert json.loads(json.dumps(insights)) == insights
