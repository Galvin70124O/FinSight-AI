"""Train the category model on dataset/seed_descriptions.csv and save ml/categoriser.joblib.

    python scripts/train_categoriser.py

Run it after changing the seed data, the rules or scikit-learn (the app only
loads a model saved by the scikit-learn version it runs). It first prints
how rules alone and rules + model do on a held-out 20% of the seed merchants,
then trains on everything and saves the file. The app never trains.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ml.categorise import MODEL_PATH, categorise, match_rule, merchant_key, normalise, save_model, train  # noqa: E402

SEED = ROOT / "dataset" / "seed_descriptions.csv"


def held_out_split(seed: pd.DataFrame, test_size: float = 0.2, random_state: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hold out whole merchants, so the model is scored on names it never saw."""
    groups = seed["description"].map(merchant_key)
    train_rows, test_rows = next(GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_state).split(seed, groups=groups))
    return seed.iloc[train_rows], seed.iloc[test_rows]


def accuracies(model, held_out: pd.DataFrame) -> tuple[float, float]:
    """Share of held-out rows given the right category by rules alone, and by rules + model.

    "Needs a category" counts as wrong in both.
    """
    rules_right = combined_right = 0
    for description, category in zip(held_out["description"], held_out["category"]):
        match = match_rule(normalise(description))
        rules_right += match is not None and match[0] == category
        keys, suggestions = categorise([description], {}, model=model)
        combined_right += suggestions[keys[0]].category == category
    return rules_right / len(held_out), combined_right / len(held_out)


def main() -> None:
    seed = pd.read_csv(SEED)
    fit_rows, held_out = held_out_split(seed)
    rules_only, combined = accuracies(train(list(fit_rows["description"]), list(fit_rows["category"])), held_out)
    print(f"Held-out rows: {len(held_out)} of {len(seed)}")
    print(f"Rules alone:   {rules_only:.1%}")
    print(f"Rules + model: {combined:.1%}")

    save_model(train(list(seed["description"]), list(seed["category"])))
    print(f"Saved {MODEL_PATH.relative_to(ROOT)} ({MODEL_PATH.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
