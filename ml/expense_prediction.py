from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.linear_model import LinearRegression


@dataclass
class ExpensePredictionResult:
    """Container for prediction output."""

    next_month: str
    predicted_expense: float
    confidence: float


class ExpensePredictionModel:
    """Simple linear regression model for expense forecasting."""

    def __init__(self) -> None:
        self.model = LinearRegression()
        self.history: list[float] = []

    def fit(self, monthly_expenses: pd.Series) -> "ExpensePredictionModel":
        """Train a linear-regression based expense policy."""
        values = monthly_expenses.astype(float).tolist()
        if len(values) < 2:
            raise ValueError("At least two monthly points are required for training.")
        self.history = values
        x = [[i] for i in range(len(values))]
        y = values
        self.model.fit(x, y)
        return self

    def predict(self, horizon: int = 1) -> float:
        """Predict the next expense value."""
        if not self.history:
            raise ValueError("The model has not been trained yet.")
        next_index = len(self.history)
        prediction = self.model.predict([[next_index]])[0]
        return max(float(prediction), 0.0)

    def save(self, path: str) -> None:
        """Persist the fitted model to disk."""
        with open(path, "wb") as handle:
            pickle.dump(self, handle)

    @classmethod
    def load(cls, path: str) -> "ExpensePredictionModel":
        """Load a previously stored model."""
        with open(path, "rb") as handle:
            return pickle.load(handle)


def train_expense_prediction(transactions_df: pd.DataFrame, model_path: str | None = None) -> ExpensePredictionResult:
    """Create a forecast for the next month from historical expense totals."""
    expenses = transactions_df[transactions_df["Type"] == "Expense"].copy()
    if expenses.empty:
        return ExpensePredictionResult(next_month="N/A", predicted_expense=0.0, confidence=0.0)

    if {"Year", "Month_Number"}.issubset(expenses.columns):
        monthly_totals = (
            expenses.groupby(["Year", "Month_Number"], as_index=False)["Amount"].sum().sort_values(["Year", "Month_Number"])
        )
        monthly_values = monthly_totals["Amount"].astype(float)
        period_label = f"{int(monthly_totals.iloc[-1]['Year'])}-{int(monthly_totals.iloc[-1]['Month_Number']):02d}"
    elif "Month" in expenses.columns:
        monthly_totals = expenses.groupby("Month")["Amount"].sum().sort_index()
        monthly_values = monthly_totals.astype(float)
        period_label = str(monthly_totals.index[-1])
    else:
        expenses = expenses.copy()
        expenses["Month"] = pd.to_datetime(expenses["Date"], errors="coerce").dt.strftime("%Y-%m")
        monthly_totals = expenses.groupby("Month")["Amount"].sum().sort_index()
        monthly_values = monthly_totals.astype(float)
        period_label = str(monthly_totals.index[-1])

    if monthly_values.empty:
        return ExpensePredictionResult(next_month="N/A", predicted_expense=0.0, confidence=0.0)

    model = ExpensePredictionModel().fit(monthly_values)
    predicted_expense = model.predict()
    if model_path:
        Path(os.path.dirname(model_path)).mkdir(parents=True, exist_ok=True)
        model.save(model_path)

    return ExpensePredictionResult(
        next_month=period_label,
        predicted_expense=round(predicted_expense, 2),
        confidence=0.82,
    )
