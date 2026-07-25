from __future__ import annotations

import os

import pandas as pd

from .anomaly_detection import detect_anomalies
from .expense_prediction import train_expense_prediction
from .health_score import calculate_financial_health_score


def run_ml_pipeline(transactions_df: pd.DataFrame, models_dir: str) -> dict[str, object]:
    """Run the full ML pipeline and persist lightweight artifacts."""
    expense_result = train_expense_prediction(
        transactions_df,
        model_path=os.path.join(models_dir, "expense_prediction_model.pkl"),
    )
    health_result = calculate_financial_health_score(
        float(transactions_df[transactions_df["Type"] == "Income"]["Amount"].sum()),
        float(transactions_df[transactions_df["Type"] == "Expense"]["Amount"].sum()),
        float((transactions_df[transactions_df["Type"] == "Income"]["Amount"].sum() - transactions_df[transactions_df["Type"] == "Expense"]["Amount"].sum()) / transactions_df[transactions_df["Type"] == "Income"]["Amount"].sum() * 100) if transactions_df[transactions_df["Type"] == "Income"]["Amount"].sum() else 0.0,
    )
    anomaly_count = detect_anomalies(
        transactions_df,
        model_path=os.path.join(models_dir, "anomaly_detector.pkl"),
    )
    return {
        "expense_prediction": expense_result,
        "health_score": health_result,
        "anomaly_count": anomaly_count,
    }
