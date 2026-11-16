"""The dashboard's Savings goals and Recurring payments sections: filled, empty, Undo once, Restore after a price change."""
import html
import re
from datetime import date

import pandas as pd

from conftest import csrf_token
from models import dismiss_recurring, get_dismissed, save_transactions

NEXT_YEAR = f"{date.today().year + 1}-06-01"


def months_of(netflix: float | None = 649.0) -> pd.DataFrame:
    rows = []
    groceries = [1200.0, 4800.0, 2500.0, 7000.0, 3900.0, 900.0]  # too uneven to be a recurring payment
    for month, grocery in zip(range(5, 11), groceries):
        rows.append({"Date": f"2026-{month:02d}-01", "Category": "Salary", "Amount": 50000.0 + month * 1000, "Type": "Income", "Description": "SALARY ACME"})
        rows.append({"Date": f"2026-{month:02d}-12", "Category": "Groceries", "Amount": grocery, "Type": "Expense", "Description": "BIGBASKET"})
        if netflix:
            rows.append({"Date": f"2026-{month:02d}-05", "Category": "Entertainment", "Amount": netflix, "Type": "Expense", "Description": "NETFLIX.COM"})
    return pd.DataFrame(rows)


def form_fields(page: str, action: str) -> dict:
    form = re.search(rf'<form method="post" action="{re.escape(action)}".*?</form>', page, re.S)
    assert form, f"no form posting to {action}"
    return {name: html.unescape(value) for name, value in re.findall(r'name="(\w+)" value="([^"]*)"', form.group(0))}


def dashboard(client) -> str:
    return client.get("/dashboard").get_data(as_text=True)


def test_empty_sections_say_what_to_do_next(client, db, logged_in):
    save_transactions(db, logged_in, months_of(netflix=None))
    page = dashboard(client)
    assert "No savings goals yet" in page and 'href="/goals/new"' in page
    assert "No recurring payments found yet" in page and "Import a statement" in page


def test_a_recurring_payment_shows_when_it_was_last_paid_and_can_be_dismissed(client, db, logged_in):
    save_transactions(db, logged_in, months_of())
    page = dashboard(client)
    assert 'id="recurring"' in page and "Last paid" in page and "5 Oct 2026" in page
    fields = form_fields(page, "/recurring/dismiss")
    client.post("/recurring/dismiss", data=fields)
    page = dashboard(client)
    assert "1 dismissed" in page and "You dismissed every recurring payment" in page
    client.post("/recurring/restore", data=form_fields(page, "/recurring/restore"))
    assert get_dismissed(db, logged_in) == set()


def test_restore_after_a_price_change_sends_back_what_was_stored(client, db, logged_in):
    save_transactions(db, logged_in, months_of(netflix=669.0))
    payee = form_fields(dashboard(client), "/recurring/dismiss")["payee"]
    dismiss_recurring(db, logged_in, payee, 30, 64900)  # dismissed when it cost ₹649
    fields = form_fields(dashboard(client), "/recurring/restore")
    assert fields["amount_paise"] == "64900"
    client.post("/recurring/restore", data=fields)
    assert get_dismissed(db, logged_in) == set()


def test_goals_show_the_shared_savings_note_and_undo_is_offered_once(client, db, logged_in):
    save_transactions(db, logged_in, months_of())
    client.post("/goals/new", data={"name": "Goa trip", "target": "60000", "target_date": NEXT_YEAR,
                                    "csrf_token": csrf_token(client, "/goals/new")})
    page = dashboard(client)
    assert "Goa trip" in page and "Added Goa trip." in page
    assert "Each date assumes all your savings go to that goal." in page

    goal_id = re.search(r'action="/goals/(\d+)/delete"', page).group(1)
    client.post(f"/goals/{goal_id}/delete", data={"csrf_token": csrf_token(client, "/goals/new")})
    page = dashboard(client)
    assert "Deleted Goa trip." in page and "No savings goals yet" in page
    undo = form_fields(page, "/goals/undo")
    assert "Deleted Goa trip." not in dashboard(client)  # shown once
    client.post("/goals/undo", data=undo)
    assert "Undone: Goa trip is back." in dashboard(client)


def test_a_goal_added_before_any_import_still_shows(client, db, logged_in):
    client.post("/goals/new", data={"name": "Bike", "target": "90000", "target_date": NEXT_YEAR,
                                    "csrf_token": csrf_token(client, "/goals/new")})
    page = dashboard(client)
    assert "Bike" in page and "No transactions yet" in page and 'id="recurring"' not in page


def test_spending_more_than_you_earn_is_said_plainly(client, db, logged_in):
    months = months_of()
    save_transactions(db, logged_in, months.assign(Amount=months["Amount"].where(months["Type"] == "Expense", 100.0)))
    page = dashboard(client)
    assert "more than you earn in an average month" in page and "You save about" not in page


def test_dismiss_is_named_once_for_screen_readers(client, db, logged_in):
    save_transactions(db, logged_in, months_of())
    page = dashboard(client)
    payee = form_fields(page, "/recurring/dismiss")["payee"]
    assert f'Dismiss<span class="sr-only"> {payee}</span>' in page


def test_sidebar_and_command_menu_link_both_sections(client, db, logged_in):
    save_transactions(db, logged_in, months_of())
    page = dashboard(client)
    assert 'href="/dashboard#goals"' in page and 'href="/dashboard#recurring"' in page
    assert '"Savings goals"' in page and '"Recurring payments"' in page and '"Add a savings goal"' in page
