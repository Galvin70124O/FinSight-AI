from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from .anomaly_detection import detect_anomalies
from .expense_prediction import forecast_expenses
from .health_score import calculate_health_score

# Bump when the shape of the insights changes so cached results are recomputed.
INSIGHTS_VERSION = 3


def compute_insights(transactions_df: pd.DataFrame) -> dict:
    """Compute one user's forecast, anomalies and health score as plain JSON-ready data.

    Nothing is written to disk and no model is shared between users; callers
    cache the returned dict per user.
    """
    if transactions_df is None or transactions_df.empty:
        return {"version": INSIGHTS_VERSION, "forecast": None, "anomalies": [], "health": None}

    df = transactions_df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df["Amount"] = df["Amount"].astype(float)
    return {
        "version": INSIGHTS_VERSION,
        "forecast": asdict(forecast_expenses(df)),
        "anomalies": detect_anomalies(df),
        "health": asdict(calculate_health_score(df)),
    }
