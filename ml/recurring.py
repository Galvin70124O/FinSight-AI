"""Recurring payments: the same payee, about the same amount, at a steady gap, three times or more."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from ml.categorise import merchant_key

# Period in days -> (what to call it, payments a year, calendar step for the next date).
PERIODS = {
    7: ("Weekly", 52, pd.DateOffset(days=7)),
    14: ("Every 2 weeks", 26, pd.DateOffset(days=14)),
    30: ("Monthly", 12, pd.DateOffset(months=1)),  # a calendar month: 31 Jan is followed by 28 Feb, not 2 Mar
    365: ("Yearly", 1, pd.DateOffset(years=1)),
}
GAP_SLACK = 3  # days either side of the period
AMOUNT_SLACK = 0.10  # every amount in a series is within 10% of the series' median
MIN_PAYMENTS = 3
DENSER_THAN = 2  # payee payments (any amount) during a series, per payment in it, beyond which it's ordinary shopping


@dataclass(frozen=True)
class Recurring:
    payee: str
    amount: float  # the typical (median) payment
    period_days: int
    frequency: str
    payments: int
    last_paid: date
    next_expected: date
    yearly_cost: float


def find_recurring(transactions: pd.DataFrame) -> list[Recurring]:
    """Every recurring payment among the expenses, highest yearly cost first.

    Payee keys come from the "Merchant" column (stored at import) where there is one, else from merchant_key().
    A series is left out once a payment is missed by more than a period (plus slack), counted to the latest
    transaction in the data, so a cancelled subscription doesn't keep a "next expected" date; and when the
    payee was paid more than DENSER_THAN times as often during it, so three of many Swiggy orders a week apart
    aren't a subscription."""
    if transactions.empty:
        return []
    when = pd.to_datetime(transactions["Date"]).to_numpy().astype("datetime64[D]")
    latest = int(when.max().astype(int))
    spent = (transactions["Type"] == "Expense").to_numpy()
    expenses = transactions[spent]
    stored = expenses["Merchant"].fillna("") if "Merchant" in expenses else pd.Series("", index=expenses.index)
    missing = stored == ""
    payees = stored.where(~missing, expenses["Description"][missing].map(merchant_key))
    days = when[spent].astype(int)  # days since 1970, for plain integer arithmetic
    amounts = expenses["Amount"].to_numpy(dtype=float)
    found = []
    for payee, rows in pd.Series(payees.to_numpy()).groupby(payees.to_numpy()).indices.items():
        if not payee:
            continue
        paid_on = np.sort(days[rows])
        by_amount = rows[np.argsort(amounts[rows], kind="stable")]
        for start, end in _amount_clusters(amounts[by_amount]):
            cluster = by_amount[start:end]
            cluster = cluster[np.argsort(days[cluster], kind="stable")]
            series = _latest_run(days[cluster])
            if series is None:
                continue
            period, first, last = series
            run = cluster[first:last + 1]
            first_day, last_day = days[run[0]], days[run[-1]]
            if latest - last_day > period + GAP_SLACK:
                continue
            during = np.searchsorted(paid_on, last_day, "right") - np.searchsorted(paid_on, first_day, "left")
            if during > DENSER_THAN * len(run):
                continue
            name, per_year, step = PERIODS[period]
            amount = round(float(np.median(amounts[run])), 2)
            last_paid = pd.Timestamp(np.datetime64(int(last_day), "D"))
            found.append(Recurring(payee, amount, period, name, len(run), last_paid.date(), (last_paid + step).date(),
                                   round(amount * per_year, 2)))
    return sorted(found, key=lambda r: (-r.yearly_cost, r.payee))


def paise(amount: float) -> int:
    return round(amount * 100)


def is_dismissed(series: Recurring, dismissed: set[tuple[str, int, int]]) -> bool:
    """Whether the user dismissed this series: same payee and period, and an amount within AMOUNT_SLACK of the
    one dismissed, so a small price change keeps it dismissed but another series of the same payee stays."""
    return any(payee == series.payee and period == series.period_days
               and abs(paise(series.amount) - amount) <= amount * AMOUNT_SLACK
               for payee, period, amount in dismissed)


def _amount_clusters(amounts: np.ndarray) -> list[tuple[int, int]]:
    """(start, end) of each group of sorted amounts that all sit within AMOUNT_SLACK of the group's median,
    smallest first. Groups too small to be a series are dropped."""
    clusters, start = [], 0
    while start < len(amounts):
        end = start + 1
        while end < len(amounts) and _fits(amounts, start, end + 1):
            end += 1
        if end - start >= MIN_PAYMENTS:
            clusters.append((start, end))
        start = end
    return clusters


def _fits(amounts: np.ndarray, start: int, end: int) -> bool:
    """Whether sorted amounts[start:end] all sit within AMOUNT_SLACK of their median (read by index, no copy)."""
    size, half = end - start, start + (end - start) // 2
    middle = amounts[half] if size % 2 else (amounts[half - 1] + amounts[half]) / 2
    return amounts[start] >= middle * (1 - AMOUNT_SLACK) and amounts[end - 1] <= middle * (1 + AMOUNT_SLACK)


def _latest_run(days: np.ndarray) -> tuple[int, int, int] | None:
    """(period, first, last) positions of the most recent run of consecutive payments (days sorted) whose every
    gap is within GAP_SLACK of one period; None when no run reaches MIN_PAYMENTS. Consecutive only: a payee
    paid every few days never chains into a weekly series, and it stays linear on a 30,000-row account.
    shortcut: a one-off of the same amount mid-series splits it in two; skip such rows if users report it."""
    periods = [next((p for p in PERIODS if abs(gap - p) <= GAP_SLACK), None) for gap in np.diff(days).tolist()]
    best, start = None, 0
    for end in range(1, len(periods) + 1):  # a run of gaps periods[start:end] ends where the period changes
        if end == len(periods) or periods[end] != periods[start]:
            if periods[start] is not None and end - start + 1 >= MIN_PAYMENTS:
                best = (periods[start], start, end)
            start = end
    return best
