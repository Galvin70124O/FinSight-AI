from __future__ import annotations

import pandas as pd

MIN_CATEGORY_SIZE = 4
# Modified z-score cut-off recommended by Iglewicz and Hoaglin.
Z_THRESHOLD = 3.5
# When every amount in a category is identical (e.g. rent), MAD is 0;
# flag amounts more than this share above the median instead.
FLAT_CATEGORY_TOLERANCE = 0.5


def detect_anomalies(transactions_df: pd.DataFrame) -> list[dict]:
    """Flag expenses that are much higher than usual for their own category.

    Each expense is compared with the median of its category, scaled by the
    median absolute deviation (MAD), so a ₹5,000 dinner stands out even if
    rent is ₹22,000. Categories with fewer than four expenses are skipped
    because there is no "normal" to compare against yet. Lower-than-usual
    amounts are never flagged: spending less is not a warning sign.
    """
    expenses = transactions_df[transactions_df["Type"] == "Expense"]
    flagged: list[dict] = []

    for category, group in expenses.groupby("Category"):
        if len(group) < MIN_CATEGORY_SIZE:
            continue
        amounts = group["Amount"].astype(float)
        median = float(amounts.median())
        mad = float((amounts - median).abs().median())

        if mad == 0:
            unusual = amounts > (1 + FLAT_CATEGORY_TOLERANCE) * median
        else:
            unusual = 0.6745 * (amounts - median) / mad > Z_THRESHOLD

        for _, row in group[unusual].iterrows():
            flagged.append(
                {
                    "date": pd.to_datetime(row["Date"]).strftime("%Y-%m-%d"),
                    "category": str(category),
                    "description": str(row["Description"]),
                    "amount": round(float(row["Amount"]), 2),
                    "typical": round(median, 2),
                    "ratio": round(float(row["Amount"]) / median, 1) if median else None,
                }
            )

    return sorted(flagged, key=lambda item: item["date"], reverse=True)
