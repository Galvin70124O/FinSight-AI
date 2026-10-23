from __future__ import annotations

import os

import pandas as pd

from .anomaly_detection import detect_anomalies
from .expense_prediction import train_expense_prediction
from .health_score import calculate_financial_health_score


def run_ml_pipeline(transactions_df: pd.DataFrame, models_dir: str) -> dict[str, object]:
    """Run the full ML pipeline and persist lightweight artifacts."""
    if transactions_df is None or transactions_df.empty:
        from .expense_prediction import ExpensePredictionResult
        from .health_score import HealthScoreResult

        return {
            "expense_prediction": ExpensePredictionResult(next_month="N/A", predicted_expense=0.0, confidence=0.0),
            "health_score": HealthScoreResult(score=0.0, label="Needs Attention"),
            "anomaly_count": 0,
            "message": "Not enough data to generate predictions yet.",
        }

    expense_result = train_expense_prediction(
        transactions_df,
        model_path=os.path.join(models_dir, "expense_prediction_model.pkl"),
    )

    if expense_result.predicted_expense == 0.0 and expense_result.confidence == 0.0:
        expense_result = expense_result.__class__(
            next_month="N/A",
            predicted_expense=0.0,
            confidence=0.0,
        )

    total_income = float(transactions_df[transactions_df["Type"] == "Income"]["Amount"].sum())
    total_expense = float(transactions_df[transactions_df["Type"] == "Expense"]["Amount"].sum())
    savings_rate = float((total_income - total_expense) / total_income * 100) if total_income else 0.0
    health_result = calculate_financial_health_score(total_income, total_expense, savings_rate)

    anomaly_count = detect_anomalies(
        transactions_df,
        model_path=os.path.join(models_dir, "anomaly_detector.pkl"),
    )

    return {
        "expense_prediction": expense_result,
        "health_score": health_result,
        "anomaly_count": anomaly_count,
        "message": "Not enough data to generate predictions yet." if expense_result.predicted_expense == 0.0 and expense_result.confidence == 0.0 else "",
    }
