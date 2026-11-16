"""Savings goals: the monthly amount a goal needs, and when it could be reached at the user's own pace."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd
from scipy import stats

from ml.expense_prediction import INTERVAL, MIN_MONTHS, monthly_expense_totals
from utils import format_inr

NAME_MAX = 60
TARGET_MAX = Decimal("100000000")  # ₹10 crore
YEARS_MAX = 50
GOALS_MAX = 20
FULL_MONTH_DAYS = 20  # days of a first or last month the data must cover for its savings to count


@dataclass(frozen=True)
class SavingsRate:
    """Income minus spending per month: the average and its 80% range. None when there are too few months."""

    months: int
    average: float | None = None
    low: float | None = None
    high: float | None = None


@dataclass(frozen=True)
class GoalPlan:
    monthly_needed: float | None  # None once the target month has passed
    eta: str  # a sentence: when it could be reached, or plainly why there is no date


def savings_rate(transactions: pd.DataFrame) -> SavingsRate:
    """The average month's savings over the same months the forecast uses, with an 80% range for that average
    (the forecast's INTERVAL, from the t-distribution): the pace a goal is saved at is the average of many
    months, not any one month. A first or last month the data covers for fewer than FULL_MONTH_DAYS days is
    left out: a statement starting on the 25th would otherwise count as a month of almost no savings."""
    spent = monthly_expense_totals(transactions)
    if not spent.empty:
        dates = pd.to_datetime(transactions["Date"])
        first, last = dates.min(), dates.max()
        keep = spent.index
        if first.days_in_month - first.day + 1 < FULL_MONTH_DAYS:
            keep = keep[1:]
        if last.day < FULL_MONTH_DAYS:
            keep = keep[:-1]
        spent = spent.reindex(keep)
    if len(spent) < MIN_MONTHS:
        return SavingsRate(len(spent))
    income = transactions[transactions["Type"] == "Income"]
    earned = income.groupby(pd.to_datetime(income["Date"]).dt.to_period("M"))["Amount"].sum()
    saved = (earned.reindex(spent.index, fill_value=0.0) - spent).to_numpy(dtype=float)
    n, average = len(saved), float(saved.mean())
    margin = float(stats.t.ppf(0.5 + INTERVAL / 2, n - 1)) * float(saved.std(ddof=1)) / math.sqrt(n)
    return SavingsRate(n, round(average, 2), round(average - margin, 2), round(average + margin, 2))


def plan_goal(target: float, target_date: date, rate: SavingsRate, today: date) -> GoalPlan:
    """What the goal needs each month until its target month, and when it could be reached if all savings went
    to it, counted from this month: "between Mar and Jun 2027"."""
    months_left = _months_between(today, target_date)
    monthly_needed = round(target / months_left, 2) if months_left > 0 else None
    if rate.average is None:
        return GoalPlan(monthly_needed, f"Import at least {MIN_MONTHS} full months of transactions to estimate a date.")
    if abs(rate.average) < 1:
        return GoalPlan(monthly_needed, "You spend about what you earn, so there's no date to give.")
    if rate.average < 0:
        return GoalPlan(monthly_needed, f"You spend more than you earn on average ({format_inr(abs(rate.average), 0)} a "
                                        "month), so there's no date to give.")
    earliest = _month_after(today, math.ceil(target / rate.high))
    if earliest is None:
        return GoalPlan(monthly_needed, f"More than {YEARS_MAX} years away at your average savings.")
    if rate.low <= 0:
        return GoalPlan(monthly_needed, f"{earliest:%b %Y} at the earliest; in slower months you'd save nothing towards it.")
    latest = _month_after(today, math.ceil(target / rate.low))
    if latest is None:
        return GoalPlan(monthly_needed, f"{earliest:%b %Y} at the earliest; in slower months it's more than {YEARS_MAX} years away.")
    if (earliest.year, earliest.month) == (latest.year, latest.month):
        return GoalPlan(monthly_needed, f"Around {earliest:%b %Y}.")
    first = f"{earliest:%b}" if earliest.year == latest.year else f"{earliest:%b %Y}"
    return GoalPlan(monthly_needed, f"Between {first} and {latest:%b %Y}.")


def _months_between(today: date, later: date) -> int:
    """Whole months of saving from this month to the target month: Nov 2026 to Mar 2027 is 4 (Dec to Mar)."""
    return (later.year - today.year) * 12 + later.month - today.month


def _month_after(today: date, months: int) -> date | None:
    """The first day of the month `months` after this one; None past YEARS_MAX."""
    if months > YEARS_MAX * 12:
        return None
    index = today.year * 12 + today.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def check_goal(form, today: date) -> tuple[dict, dict]:
    """The goal form's values, cleaned, and a message for each field that's wrong. Server-side: the form's own
    checks are only a convenience."""
    values = {key: (form.get(key) or "").strip() for key in ("name", "target", "target_date")}
    errors = {}
    if not values["name"]:
        errors["name"] = "Give the goal a name."
    elif len(values["name"]) > NAME_MAX:
        errors["name"] = f"Keep the name to {NAME_MAX} characters (it has {len(values['name'])})."
    text = values["target"].replace("₹", "").replace(",", "").replace(" ", "")
    try:
        target = Decimal(text)
        if not target.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        errors["target"] = "Enter the target in rupees, such as 1,50,000."
    else:
        if target <= 0:
            errors["target"] = "The target must be more than ₹0."
        elif target > TARGET_MAX:
            errors["target"] = "Keep the target to ₹10 crore or less."
        elif target.as_tuple().exponent < -2:
            errors["target"] = "Use at most 2 decimal places (paise)."
        else:
            values["target_amount"] = float(target)
    try:
        when = date.fromisoformat(values["target_date"])
    except ValueError:
        errors["target_date"] = "Pick a target date."
    else:
        if _months_between(today, when) < 1:
            errors["target_date"] = "Pick a date in a later month, so there's at least a month to save."
        elif _months_between(today, when) > YEARS_MAX * 12:
            errors["target_date"] = f"Pick a date within {YEARS_MAX} years."
        else:
            values["date"] = when
    return values, errors
