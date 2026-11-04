"""Fill in missing categories from bank descriptions such as "UPI/ZOMATO/4521/...".

Three layers, in this order:

1. The user's own corrections, by merchant. These always win.
2. Keyword rules from ``merchant_rules.json``.
3. A small model (TF-IDF on 3-5 character pieces + logistic regression),
   trained offline on fictional seed descriptions by
   ``scripts/train_categoriser.py``. Below 60% confidence the row is left
   for the user ("Needs a category").

Nothing here trains at request time and no user data reaches the model.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import joblib
import sklearn
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

logger = logging.getLogger(__name__)

_RULES_FILE = json.loads(Path(__file__).with_name("merchant_rules.json").read_text(encoding="utf-8"))
CATEGORIES: list[str] = _RULES_FILE["categories"]
RULES: list[tuple[str, list[str]]] = [(rule["category"], rule["keywords"]) for rule in _RULES_FILE["rules"]]
assert all(category in CATEGORIES for category, _ in RULES), "every rule needs a category from the list"

MODEL_PATH = Path(__file__).with_name("categoriser.joblib")
CONFIDENCE_THRESHOLD = 0.6
UNCATEGORISED = "Uncategorized"  # what a row with no chosen category is saved as, as elsewhere in the app

_MONTH = r"(?:JAN(?:UARY)?|FEB(?:RUARY)?|MAR(?:CH)?|APR(?:IL)?|MAY|JUNE?|JULY?|AUG(?:UST)?|SEP(?:T|TEMBER)?|OCT(?:OBER)?|NOV(?:EMBER)?|DEC(?:EMBER)?)"
_DATE = re.compile(rf"\b\d{{1,2}}[-/. ]?{_MONTH}[-/. ]?\d{{2,4}}\b|\b\d{{1,4}}[-/.]\d{{1,2}}[-/.]\d{{1,4}}\b")
_HANDLE = re.compile(r"@[A-Z][\w.]*")  # the @okaxis of a UPI handle (or an email's domain); the name before it stays
_IFSC = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")  # bank branch codes such as HDFC0001234
_AUTOPAY = re.compile(r"\bAUTO ?(?:DEBIT|PAY|DR)\b")  # a payment method, not an auto-rickshaw
_SEGMENT = re.compile(r"[/\-*:|\\]+")
# Payment channels and card networks: noise wherever they appear.
_NOISE = {"UPI", "NEFT", "IMPS", "RTGS", "ACH", "NACH", "ECS", "POS", "P2M", "P2A", "MMT", "INFT", "BIL", "BPAY",
          "VPS", "VISA", "RUPAY", "MASTERCARD", "WWW", "COM", "UTR", "RRN"}
# Direction words banks put in front: noise only at the start ("DR MEHTA" later on is a doctor).
_LEADING_NOISE = {"CR", "DR", "D", "C", "TO", "BY", "TRF", "TRANSFER", "REF", "SENT", "RECEIVED"}
_COMPANY_SUFFIX = {"LTD", "LIMITED", "PVT", "PRIVATE", "LLP", "INC", "CO"}


def _clean_segments(description: object) -> list[str]:
    text = str(description or "").upper()
    for pattern in (_HANDLE, _IFSC, _DATE, _AUTOPAY):
        text = pattern.sub(" ", text)
    segments = []
    for segment in _SEGMENT.split(text):
        tokens = re.sub(r"[^A-Z0-9&]+", " ", segment).split()
        # References, account and card numbers: any word that is all digits or has three or more.
        tokens = [t for t in tokens if not t.isdigit() and sum(ch.isdigit() for ch in t) < 3 and t not in _NOISE]
        if not segments:
            while tokens and tokens[0] in _LEADING_NOISE:
                tokens.pop(0)
        if tokens:
            segments.append(" ".join(tokens))
    return segments


def normalise(description: object) -> str:
    """Upper-case the description and strip channel prefixes, references, dates and UPI handles."""
    return " ".join(_clean_segments(description))


def merchant_key(description: object) -> str:
    """The merchant a row belongs to: the first meaningful part of the description, e.g. "ZOMATO".

    Empty when the description has no name in it at all.
    """
    segments = _clean_segments(description)
    if not segments:
        return ""
    words = segments[0].split()
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIX:
        words.pop()
    return " ".join(words[:3])[:40]


def match_rule(normalised: str) -> tuple[str, str] | None:
    """(category, keyword) for the first rule with a keyword in the text, matched as whole words."""
    padded = f" {normalised} "
    for category, keywords in RULES:
        for keyword in keywords:
            if f" {keyword} " in padded:
                return category, keyword
    return None


def train(descriptions: list[str], categories: list[str]):
    """Fit the model on raw descriptions. Used by the training script and the tests."""
    model = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True),
        LogisticRegression(C=50, max_iter=2000),  # chosen on 10 merchant splits: ~2% wrong, half left for the user
    )
    return model.fit([normalise(d) for d in descriptions], categories)


def save_model(model, path: Path = MODEL_PATH) -> None:
    joblib.dump({"model": model, "sklearn": sklearn.__version__}, path, compress=3)


def load_model(path: Path = MODEL_PATH):
    """The saved model, or None (corrections and rules still work) if it is missing or was saved by another scikit-learn."""
    try:
        # joblib unpickles: only ever load our own file from the repo, never anything a user supplies.
        saved = joblib.load(path)
    except Exception:
        logger.exception("Category model %s could not be loaded; using corrections and rules only.", path)
        return None
    if not isinstance(saved, dict) or saved.get("sklearn") != sklearn.__version__:
        logger.error("Category model %s was not saved by the installed scikit-learn %s; using corrections and rules only. "
                     "Run scripts/train_categoriser.py.", path, sklearn.__version__)
        return None
    return saved["model"]


MODEL = load_model()


@dataclass
class Suggestion:
    category: str | None  # None means "Needs a category"
    source: str  # "correction", "rule", "model" or "none"
    keyword: str = ""  # the rule keyword that matched
    confidence: float | None = None  # the model's confidence


def categorise(descriptions: list[str], overrides: dict[str, str], model=None) -> tuple[list[str], dict[str, Suggestion]]:
    """Group rows by merchant and suggest one category per merchant.

    Returns each row's merchant key and a suggestion per key.
    """
    model = MODEL if model is None else model
    keys = [merchant_key(d) for d in descriptions]
    texts = [normalise(d) for d in descriptions]
    groups: dict[str, list[int]] = {}
    for index, key in enumerate(keys):
        groups.setdefault(key, []).append(index)

    suggestions: dict[str, Suggestion] = {}
    for_model: dict[str, list[int]] = {}
    for key, rows in groups.items():
        if key and key in overrides:
            suggestions[key] = Suggestion(overrides[key], "correction")
            continue
        matches = [match for match in (match_rule(texts[i]) for i in rows) if match]
        if matches:
            category, keyword = Counter(matches).most_common(1)[0][0]
            suggestions[key] = Suggestion(category, "rule", keyword=keyword)
        else:
            for_model[key] = rows

    if for_model and model is not None:
        probabilities = model.predict_proba([texts[i] for rows in for_model.values() for i in rows])
        start = 0
        for key, rows in for_model.items():
            mean = probabilities[start:start + len(rows)].mean(axis=0)
            start += len(rows)
            best = int(mean.argmax())
            confidence = float(mean[best])
            category = str(model.classes_[best]) if confidence >= CONFIDENCE_THRESHOLD else None
            suggestions[key] = Suggestion(category, "model", confidence=confidence)
    for key in for_model:
        suggestions.setdefault(key, Suggestion(None, "none"))
    return keys, suggestions
