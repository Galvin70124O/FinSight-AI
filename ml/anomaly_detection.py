from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

import pandas as pd
from sklearn.ensemble import IsolationForest


class AnomalyDetectionModel:
    """Isolation Forest based anomaly detector for unusual transactions."""

    def __init__(self, contamination: float = 0.05) -> None:
        self.model = IsolationForest(contamination=contamination, random_state=42)

    def fit(self, transactions_df: pd.DataFrame) -> "AnomalyDetectionModel":
        """Fit the anomaly detector on transaction amounts."""
        values = transactions_df[["Amount"]].astype(float)
        self.model.fit(values)
        return self

    def predict(self, transactions_df: pd.DataFrame) -> list[bool]:
        """Return a boolean mask for anomalies."""
        values = transactions_df[["Amount"]].astype(float)
        labels = self.model.predict(values)
        return [label == -1 for label in labels]

    def save(self, path: str) -> None:
        """Persist the detector to disk."""
        with open(path, "wb") as handle:
            pickle.dump(self, handle)


def detect_anomalies(transactions_df: pd.DataFrame, model_path: str | None = None) -> int:
    """Count suspicious transactions based on amount anomalies."""
    if transactions_df.empty:
        return 0
    detector = AnomalyDetectionModel().fit(transactions_df)
    if model_path:
        Path(os.path.dirname(model_path)).mkdir(parents=True, exist_ok=True)
        detector.save(model_path)
    anomalies = detector.predict(transactions_df)
    return int(sum(anomalies))
