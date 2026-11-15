"""Savings goals: checking the form, the monthly amount needed, and the date range at the user's savings pace."""
import uuid
from datetime import date

import pandas as pd
import pytest

from ml.goals import SavingsRate, check_goal, plan_goal, savings_rate
from models import create_goal, create_user, delete_goal, get_goal, get_goals, update_goal

TODAY = date(2026, 11, 15)
GOOD = {"name": "Goa trip", "target": "60,000", "target_date": "2027-06-01"}


def two_users(db):
    """Two users with fresh names: the embedded Postgres is shared by every test in a file."""
    return tuple(create_user(db, f"{name}{uuid.uuid4().hex[:8]}", f"{uuid.uuid4().hex[:12]}@example.com", "x" * 12)
                 for name in ("asha", "ravi"))


@pytest.mark.parametrize("field, value, message", [
    ("name", "  ", "Give the goal a name."),
    ("name", "x" * 61, "Keep the name to 60 characters (it has 61)."),
    ("target", "", "Enter the target in rupees, such as 1,50,000."),
    ("target", "sixty thousand", "Enter the target in rupees, such as 1,50,000."),
    ("target", "NaN", "Enter the target in rupees, such as 1,50,000."),
    ("target", "0", "The target must be more than ₹0."),
    ("target", "-500", "The target must be more than ₹0."),
    ("target", "10,00,00,001", "Keep the target to ₹10 crore or less."),
    ("target", "500.555", "Use at most 2 decimal places (paise)."),
    ("target_date", "", "Pick a target date."),
    ("target_date", "2027-02-30", "Pick a target date."),
    ("target_date", "2026-11-30", "Pick a date in a later month, so there's at least a month to save."),
    ("target_date", "2076-12-01", "Pick a date within 50 years."),
])
def test_each_wrong_field_gets_its_own_message(field, value, message):
    values, errors = check_goal({**GOOD, field: value}, TODAY)
    assert errors == {field: message}


def test_a_good_goal_is_cleaned():
    values, errors = check_goal({"name": " Goa trip ", "target": "₹ 1,50,000.50", "target_date": "2027-06-01"}, TODAY)
    assert errors == {} and (values["name"], values["target_amount"], values["date"]) == ("Goa trip", 150000.5, date(2027, 6, 1))


RATE = SavingsRate(months=8, average=10000.0, low=8000.0, high=12000.0)


@pytest.mark.parametrize("target, rate, eta", [
    (60000, RATE, "Between Apr and Jul 2027."),  # 5 to 8 months from November
    (150000, RATE, "Between Dec 2027 and Jun 2028."),
    (60000, SavingsRate(8, 10000.0, 10000.0, 10000.0), "Around May 2027."),
    (60000, SavingsRate(8, 3000.0, -500.0, 6500.0), "Sep 2027 at the earliest; in slower months you'd save nothing towards it."),
    (60000, SavingsRate(8, -2300.0, -4000.0, -600.0), "You spend more than you earn on average (₹2,300 a month), so there's no date to give."),
    (60000, SavingsRate(8, 0.0, -900.0, 900.0), "You spend about what you earn, so there's no date to give."),
    (60000, SavingsRate(2), "Import at least 3 months of transactions to estimate a date."),
    (10_00_00_000, SavingsRate(8, 1000.0, 900.0, 1100.0), "More than 50 years away at your average savings."),
])
def test_the_date_is_a_range_at_the_users_pace_or_says_plainly_why_not(target, rate, eta):
    assert plan_goal(target, date(2027, 11, 1), rate, TODAY).eta == eta


def test_the_monthly_amount_spreads_the_target_over_the_months_left():
    assert plan_goal(120000, date(2027, 11, 1), RATE, TODAY).monthly_needed == 10000.0  # Dec 2026 to Nov 2027
    assert plan_goal(120000, date(2026, 10, 1), RATE, TODAY).monthly_needed is None  # the month has passed


def test_savings_rate_is_income_minus_spending_with_a_range_around_the_average():
    rows = [("2026-0%d-01" % m, 50000, "Income") for m in (1, 2, 3)]
    rows += [("2026-01-10", 40000, "Expense"), ("2026-02-10", 42000, "Expense"), ("2026-03-10", 38000, "Expense")]
    df = pd.DataFrame([{"Date": d, "Amount": a, "Type": t, "Category": "", "Description": ""} for d, a, t in rows])
    rate = savings_rate(df)
    assert (rate.months, rate.average) == (3, 10000.0)
    assert rate.low < 10000 < rate.high and round(rate.high - 10000, 2) == round(10000 - rate.low, 2)
    assert savings_rate(df[df["Date"] < "2026-03-01"]) == SavingsRate(2)


def test_one_users_goals_are_out_of_another_users_reach(engine_db):
    db = engine_db
    asha, ravi = two_users(db)
    goal = create_goal(db, asha, "Goa trip", 60000.0, "2027-06-01")
    assert get_goals(db, ravi) == [] and get_goal(db, ravi, goal) is None
    assert update_goal(db, ravi, goal, "Mine now", 1.0, "2027-06-01") is False
    assert delete_goal(db, ravi, goal) is None
    assert get_goal(db, asha, goal) == {"id": goal, "name": "Goa trip", "target_amount": 60000.0, "target_date": "2027-06-01"}
    assert delete_goal(db, asha, goal) == {"name": "Goa trip", "target_amount": 60000.0, "target_date": "2027-06-01"}
    assert get_goals(db, asha) == []
