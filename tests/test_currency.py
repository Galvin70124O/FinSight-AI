import pytest

from utils import format_inr, inr_axis


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "₹0"),
        (999, "₹999"),
        (1000, "₹1,000"),
        (125000, "₹1,25,000"),
        (12345678, "₹1,23,45,678"),
        (1250.5, "₹1,250.50"),
        (-2500, "−₹2,500"),
        (None, "₹0"),
    ],
)
def test_format_inr_uses_indian_grouping(value, expected):
    assert format_inr(value) == expected


def test_axis_ticks_are_labelled_in_rupees():
    axis = inr_axis(240000)
    assert axis["ticktext"][0] == "₹0"
    assert "₹1,00,000" in axis["ticktext"]
    assert axis["tickvals"][-1] >= 240000


def test_dashboard_shows_rupees_not_dollars(client, logged_in):
    from conftest import csrf_token

    page = client.post("/demo-data", data={"csrf_token": csrf_token(client, "/dashboard")}, follow_redirects=True)
    html = page.get_data(as_text=True)

    assert "₹" in html
    assert "$" not in html.replace("${", "")  # ignore JS template literals


def test_dates_read_as_day_month_year():
    from utils import format_day

    assert format_day("2025-04-27") == "27 Apr 2025"
    assert format_day("2025-04-03") == "3 Apr 2025"


def test_tables_show_readable_dates_but_sort_on_iso(client, logged_in):
    import re

    from conftest import csrf_token

    html = client.post("/demo-data", data={"csrf_token": csrf_token(client, "/dashboard")}, follow_redirects=True).get_data(as_text=True)
    table = html[html.index('id="transactionsTable"'):]
    assert re.search(r'data-date="\d{4}-\d{2}-\d{2}"', table)  # sorting key stays ISO
    assert re.search(r'tx-date">\d{1,2} [A-Z][a-z]{2} \d{4}</td>', table)
    assert not re.search(r">\d{4}-\d{2}-\d{2}<", table)
