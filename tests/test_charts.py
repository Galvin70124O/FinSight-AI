import json

import pandas as pd

from utils import build_dashboard_payload

ROWS = [
    {"Date": f"2025-0{m}-05", "Category": "Food", "Amount": 1000.0 + m * 100, "Type": "Expense", "Description": "x"}
    for m in range(1, 7)
] + [{"Date": "2025-06-01", "Category": "Salary", "Amount": 9000.0, "Type": "Income", "Description": "pay"}]

FORECAST = {"enough_data": True, "month": "Jul 2025", "predicted": 1700.0, "low": 1500.0, "high": 1900.0}


def charts(app, forecast=FORECAST):
    with app.app_context():
        return build_dashboard_payload(pd.DataFrame(ROWS), {"forecast": forecast})["charts"]


def test_charts_are_plain_json_not_html(app):
    data = charts(app)
    assert json.loads(json.dumps(data["spending"])) == data["spending"]
    assert json.loads(json.dumps(data["cashflow"])) == data["cashflow"]


def test_spending_line_runs_into_the_forecast_band(app):
    traces = {t["meta"]: t for t in charts(app)["spending"]["data"]}

    assert traces["actual"]["x"][-1] == "Jun 2025"
    assert traces["band-low"]["x"] == traces["band"]["x"] == ["Jun 2025", "Jul 2025"]
    assert traces["band-low"]["y"] == [1600.0, 1500.0]  # zero-width at the last real month
    assert traces["band"]["y"] == [1600.0, 1900.0]
    assert traces["band"]["fill"] == "tonexty"
    assert traces["forecast"]["y"] == [1600.0, 1700.0]


def test_month_axis_is_tall_enough_for_the_band(app):
    month = charts(app)["spending_views"]["month"]
    assert max(month["tickvals"]) >= FORECAST["high"]


def test_no_band_without_a_forecast(app):
    traces = charts(app, {"enough_data": False})["spending"]["data"]
    assert [t["meta"] for t in traces] == ["actual"]


def test_income_and_spending_are_bars(app):
    traces = charts(app)["cashflow"]["data"]
    assert [(t["type"], t["meta"]) for t in traces] == [("bar", "income"), ("bar", "spending")]
