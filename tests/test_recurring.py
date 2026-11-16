"""Recurring payments: found from the same payee at a steady amount and gap, and dismissed per user."""
import uuid
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ml.recurring import find_recurring, is_dismissed, paise
from models import create_user, dismiss_recurring, get_dismissed, restore_recurring

SAMPLE = Path(__file__).resolve().parents[1] / "dataset" / "sample_transactions.csv"


def frame(rows):
    """rows: (date, amount, description[, type]); every row an expense unless it says otherwise."""
    return pd.DataFrame([{"Date": str(d), "Amount": a, "Description": text, "Type": rest[0] if rest else "Expense",
                          "Category": ""} for d, a, text, *rest in rows])


def every(start: date, days: int, count: int, amount: float, text: str):
    return [(start + timedelta(days=days * i), amount, text) for i in range(count)]


def two_users(db):
    """Two users with fresh names: the embedded Postgres is shared by every test in a file."""
    return tuple(create_user(db, f"{name}{uuid.uuid4().hex[:8]}", f"{uuid.uuid4().hex[:12]}@example.com", "x" * 12)
                 for name in ("asha", "ravi"))


@pytest.mark.parametrize("dates, frequency, next_expected, per_year", [
    ([date(2026, 1, 5) + timedelta(days=7 * i) for i in range(4)], "Weekly", date(2026, 2, 2), 52),
    ([date(2026, 1, 5) + timedelta(days=14 * i) for i in range(4)], "Every 2 weeks", date(2026, 3, 2), 26),
    ([date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)], "Monthly", date(2026, 5, 30), 12),
    ([date(2023, 3, 1), date(2024, 3, 1), date(2025, 3, 1)], "Yearly", date(2026, 3, 1), 1),
])
def test_each_frequency_is_found_with_its_next_date_and_yearly_cost(dates, frequency, next_expected, per_year):
    [found] = find_recurring(frame([(d, 649.0, "NACH AUTO DEBIT/NETFLIX.COM/SI 7788") for d in dates]))
    assert (found.frequency, found.payments, found.last_paid, found.next_expected) == (frequency, len(dates), dates[-1], next_expected)
    assert found.amount == 649.0 and found.yearly_cost == 649.0 * per_year


@pytest.mark.parametrize("amounts, found", [([500, 500, 550], True), ([500, 500, 555], False)])
def test_amounts_within_ten_percent_of_the_median_count_and_more_do_not(amounts, found):
    rows = [(date(2026, 1, 1) + timedelta(days=30 * i), amount, "UPI/BESCOM/123") for i, amount in enumerate(amounts)]
    assert bool(find_recurring(frame(rows))) is found


@pytest.mark.parametrize("gap, found", [(33, True), (27, True), (34, False), (26, False)])
def test_gaps_within_three_days_of_a_period_count_and_four_do_not(gap, found):
    assert bool(find_recurring(frame(every(date(2026, 1, 1), gap, 3, 399.0, "UPI/JIO PREPAID/1")))) is found


def test_two_payments_or_income_are_not_recurring_payments():
    assert find_recurring(frame(every(date(2026, 1, 1), 30, 2, 649.0, "NETFLIX"))) == []
    salary = [(d, amount, text, "Income") for d, amount, text in every(date(2026, 1, 1), 30, 6, 85000.0, "ACH/SALARY")]
    assert find_recurring(frame(salary)) == []


def test_one_payee_can_have_two_series():
    rows = every(date(2026, 1, 1), 30, 4, 649.0, "NETFLIX") + every(date(2026, 1, 10), 30, 4, 199.0, "NETFLIX")
    assert [(r.amount, r.payments) for r in find_recurring(frame(rows))] == [(649.0, 4), (199.0, 4)]


def test_a_payee_paid_every_few_days_is_not_a_weekly_payment():
    # Three ₹300 orders a week apart, among orders every other day at other amounts: shopping, not a subscription.
    rows = every(date(2026, 1, 1), 7, 3, 300.0, "UPI/SWIGGY/1")
    rows += [(date(2026, 1, 2) + timedelta(days=2 * i), 150.0 + 40 * (i % 9), "UPI/SWIGGY/2") for i in range(8)]
    assert find_recurring(frame(rows)) == []
    rows = every(date(2026, 1, 1), 7, 3, 300.0, "UPI/SWIGGY/1") + [(date(2026, 1, 4), 620.0, "UPI/SWIGGY/2")]
    assert [r.payee for r in find_recurring(frame(rows))] == ["SWIGGY"]  # one other order in three weeks is fine


def test_a_series_that_stopped_is_hidden_counted_to_the_latest_transaction():
    rows = every(date(2026, 1, 1), 30, 3, 649.0, "NETFLIX")  # last paid 2 Mar
    assert find_recurring(frame(rows + [(date(2026, 4, 4), 10.0, "TEA")]))  # 33 days on: not missed yet
    assert find_recurring(frame(rows + [(date(2026, 4, 5), 10.0, "TEA")])) == []  # 34 days: a payment was missed


def test_highest_yearly_cost_comes_first_and_the_sample_data_finds_its_bills():
    rows = (every(date(2026, 1, 5), 7, 9, 100.0, "UPI/MILK BASKET/1") + every(date(2026, 1, 1), 30, 3, 649.0, "NETFLIX")
            + [(date(2022 + i, 3, 1), 1499.0, "AMAZON PRIME") for i in range(3)])
    assert [r.payee for r in find_recurring(frame(rows))] == ["NETFLIX", "MILK BASKET"]  # Prime stopped after 2024
    assert [r.payee for r in find_recurring(pd.read_csv(SAMPLE))] == [
        "FLAT RENT", "BROADBAND", "STREAMING SUBSCRIPTION", "MOBILE RECHARGE"]


def test_dismissing_one_series_keeps_the_other_series_of_the_same_payee(engine_db):
    rows = every(date(2026, 1, 1), 30, 4, 649.0, "NETFLIX") + every(date(2026, 1, 10), 30, 4, 199.0, "NETFLIX")
    big, small = find_recurring(frame(rows))
    db = engine_db
    asha, _ = two_users(db)
    dismiss_recurring(db, asha, small.payee, small.period_days, paise(small.amount))
    dismissed = get_dismissed(db, asha)
    assert is_dismissed(small, dismissed) and not is_dismissed(big, dismissed)
    pricier = find_recurring(frame(every(date(2026, 1, 10), 30, 4, 215.0, "NETFLIX")))[0]  # an 8% price rise
    assert is_dismissed(pricier, dismissed)


def test_a_dismissal_belongs_to_one_user_only(engine_db):
    db = engine_db
    asha, ravi = two_users(db)
    dismiss_recurring(db, asha, "NETFLIX", 30, 64900)
    dismiss_recurring(db, asha, "NETFLIX", 30, 64900)  # twice is once
    assert get_dismissed(db, asha) == {("NETFLIX", 30, 64900)} and get_dismissed(db, ravi) == set()
    restore_recurring(db, ravi, "NETFLIX", 30, 64900)  # Ravi can't undo Asha's choice
    assert get_dismissed(db, asha) == {("NETFLIX", 30, 64900)}
    restore_recurring(db, asha, "NETFLIX", 30, 64900)
    assert get_dismissed(db, asha) == set()
