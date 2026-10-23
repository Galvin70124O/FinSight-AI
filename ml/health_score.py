from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

TARGET_SAVINGS_RATE = 0.20
NO_INCOME_MESSAGE = "Add income transactions to see your health score."


@dataclass
class HealthScore:
    """Financial health score out of 100 and the parts it is built from."""

    score: float | None
    label: str
    components: list[dict] = field(default_factory=list)
    message: str = ""


def calculate_health_score(transactions_df: pd.DataFrame) -> HealthScore:
    """The one place the health score is calculated.

    - Savings rate (50 points): full marks at 20% of income saved or more.
    - Months covered (25 points): share of months where income covered spending.
    - Steady spending (25 points): lower month-to-month variation scores higher;
      needs at least two months, otherwise it is left out and the score is
      rescaled to the parts that could be measured.
    """
    income = transactions_df[transactions_df["Type"] == "Income"]
    expenses = transactions_df[transactions_df["Type"] == "Expense"]
    total_income = float(income["Amount"].sum())
    if total_income <= 0:
        return HealthScore(score=None, label="No income yet", message=NO_INCOME_MESSAGE)

    dates = pd.to_datetime(transactions_df["Date"])
    months = pd.period_range(dates.min().to_period("M"), dates.max().to_period("M"), freq="M")

    def per_month(frame: pd.DataFrame) -> pd.Series:
        grouped = frame.groupby(pd.to_datetime(frame["Date"]).dt.to_period("M"))["Amount"].sum()
        return grouped.reindex(months, fill_value=0.0)

    monthly_income, monthly_expense = per_month(income), per_month(expenses)

    savings_rate = (total_income - float(expenses["Amount"].sum())) / total_income
    covered = int((monthly_income >= monthly_expense).sum())
    components = [
        {
            "name": "Savings rate",
            "points": round(50 * min(max(savings_rate / TARGET_SAVINGS_RATE, 0.0), 1.0), 1),
            "max_points": 50,
            "detail": f"You kept {savings_rate * 100:.1f}% of your income (full marks at 20%).",
        },
        {
            "name": "Months covered",
            "points": round(25 * covered / len(months), 1),
            "max_points": 25,
            "detail": f"Income covered spending in {covered} of {len(months)} month(s).",
        },
    ]
    if len(months) >= 2 and monthly_expense.mean() > 0:
        variation = float(monthly_expense.std(ddof=0) / monthly_expense.mean())
        components.append(
            {
                "name": "Steady spending",
                "points": round(25 * min(max(1 - variation, 0.0), 1.0), 1),
                "max_points": 25,
                "detail": f"Monthly spending varies by about {variation * 100:.0f}% around its average.",
            }
        )

    score = round(100 * sum(c["points"] for c in components) / sum(c["max_points"] for c in components), 1)
    if score >= 80:
        label = "Healthy"
    elif score >= 60:
        label = "Stable"
    else:
        label = "Needs attention"
    return HealthScore(score=score, label=label, components=components)
