from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HealthScoreResult:
    """Collected output from the health scoring module."""

    score: float
    label: str


def calculate_financial_health_score(total_income: float, total_expense: float, savings_rate: float) -> HealthScoreResult:
    """Generate a simple health score for the user's finances."""
    base_score = 100 - (total_expense / total_income * 100 if total_income else 0) * 1.2
    score = round(max(min(base_score + (savings_rate * 0.4), 100), 0), 1)
    if score >= 80:
        label = "Healthy"
    elif score >= 60:
        label = "Stable"
    else:
        label = "Needs Attention"
    return HealthScoreResult(score=score, label=label)
