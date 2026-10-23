import json
import re

import pandas as pd

from conftest import csrf_token
from utils import build_dashboard_payload

ROWS = [
    {"Date": "2024-11-05", "Category": "Food", "Amount": 1000.0, "Type": "Expense", "Description": "a"},
    {"Date": "2024-12-05", "Category": "Food", "Amount": 2000.0, "Type": "Expense", "Description": "b"},
    {"Date": "2025-01-05", "Category": "Food", "Amount": 4000.0, "Type": "Expense", "Description": "c"},
    {"Date": "2025-02-05", "Category": "Food", "Amount": 125000.0, "Type": "Expense", "Description": "d"},
]


def test_each_range_has_its_own_view(app):
    with app.app_context():
        views = build_dashboard_payload(pd.DataFrame(ROWS))["charts"]["spending_views"]

    assert views["month"]["x"] == ["Nov 2024", "Dec 2024", "Jan 2025", "Feb 2025"]
    assert views["quarter"]["x"] == ["Q4 2024", "Q1 2025"]
    assert views["quarter"]["y"] == [3000.0, 129000.0]
    assert views["year"]["x"] == ["2024", "2025"]
    assert views["quarter"]["customdata"][1] == ["₹1,29,000", 2]
    assert views["year"]["axis_title"] == "Year"
    assert views["year"]["ticktext"][0] == "₹0"


def test_views_match_chart_series(app):
    with app.app_context():
        payload = build_dashboard_payload(pd.DataFrame(ROWS))

    series = payload["summary"]["chart_series"]
    views = payload["charts"]["spending_views"]
    for range_name, series_name in (("month", "monthly"), ("quarter", "quarterly"), ("year", "yearly")):
        assert views[range_name]["x"] == series[series_name]["labels"]
        assert views[range_name]["y"] == series[series_name]["values"]


def test_dashboard_embeds_views_for_the_buttons(client, logged_in):
    page = client.post("/demo-data", data={"csrf_token": csrf_token(client, "/dashboard")}, follow_redirects=True)
    html = page.get_data(as_text=True)

    assert 'id="spendingChart"' in html
    for range_name in ("month", "quarter", "year"):
        assert f'data-range="{range_name}"' in html

    embedded = re.search(r'<script type="application/json" id="spendingViews">(.*?)</script>', html, re.S)
    views = json.loads(embedded.group(1))
    assert views["quarter"]["x"][0] == "Q3 2024"
    assert views["year"]["x"] == ["2024", "2025"]


def test_empty_dashboard_has_no_range_buttons(client, logged_in):
    html = client.get("/dashboard").get_data(as_text=True)
    assert 'data-range="quarter"' not in html
