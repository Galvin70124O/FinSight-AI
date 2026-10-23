from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LinearRegression

MIN_MONTHS = 3
CONFIDENT_MONTHS = 6
INTERVAL = 0.80


@dataclass
class ExpenseForecast:
    """Next month's expected spending with an 80% prediction interval."""

    enough_data: bool
    months_used: int
    month: str | None = None
    predicted: float | None = None
    low: float | None = None
    high: float | None = None
    low_confidence: bool = False
    message: str = ""


def monthly_expense_totals(transactions_df: pd.DataFrame) -> pd.Series:
    """Total expenses per year-month, oldest first, with months without spending as 0.

    The range runs from the first to the last month with any transaction, so a
    month with only income still counts as a month of history.
    """
    if transactions_df.empty:
        return pd.Series(dtype=float)
    dates = pd.to_datetime(transactions_df["Date"])
    months = pd.period_range(dates.min().to_period("M"), dates.max().to_period("M"), freq="M")
    expenses = transactions_df[transactions_df["Type"] == "Expense"]
    totals = expenses.groupby(pd.to_datetime(expenses["Date"]).dt.to_period("M"))["Amount"].sum()
    return totals.reindex(months, fill_value=0.0).astype(float)


def forecast_expenses(transactions_df: pd.DataFrame) -> ExpenseForecast:
    """Fit a straight-line trend to monthly totals and forecast the next month.

    The range is a standard regression prediction interval built from how far
    past months sat from the trend line, so it widens when spending is erratic
    or history is short.
    """
    totals = monthly_expense_totals(transactions_df)
    n = len(totals)
    if n < MIN_MONTHS:
        return ExpenseForecast(
            enough_data=False,
            months_used=n,
            message=f"Need at least {MIN_MONTHS} months of data to forecast spending (you have {n}).",
        )

    x = np.arange(n, dtype=float)
    y = totals.to_numpy()
    model = LinearRegression().fit(x.reshape(-1, 1), y)
    predicted = float(model.predict([[n]])[0])

    residuals = y - model.predict(x.reshape(-1, 1))
    dof = n - 2
    residual_std = float(np.sqrt(np.sum(residuals**2) / dof))
    leverage = 1 + 1 / n + (n - x.mean()) ** 2 / np.sum((x - x.mean()) ** 2)
    margin = float(stats.t.ppf(0.5 + INTERVAL / 2, dof)) * residual_std * float(np.sqrt(leverage))

    low_confidence = n < CONFIDENT_MONTHS
    return ExpenseForecast(
        enough_data=True,
        months_used=n,
        month=(totals.index[-1] + 1).strftime("%b %Y"),
        predicted=round(max(predicted, 0.0), 2),
        low=round(max(predicted - margin, 0.0), 2),
        high=round(max(predicted + margin, 0.0), 2),
        low_confidence=low_confidence,
        message=f"Low confidence: based on only {n} months." if low_confidence else f"Based on {n} months of history.",
    )
