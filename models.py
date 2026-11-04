from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from werkzeug.security import check_password_hash, generate_password_hash

from database import IntegrityError


@dataclass
class User:
    """User model used for authentication and account representation."""

    id: int | None
    username: str
    email: str
    password_hash: str

    @classmethod
    def from_row(cls, row: Any) -> "User":
        return cls(
            id=row["id"],
            username=row["username"],
            email=row["email"],
            password_hash=row["password_hash"],
        )


@dataclass
class Transaction:
    """Transaction domain object."""

    id: int | None
    user_id: int
    date: str
    category: str
    amount: float
    type: str
    description: str
    source_file: str | None = None


def transaction_signature(date_value: Any, amount: Any, transaction_type: Any, description: Any) -> tuple[str, float, str, str]:
    """Create a stable signature for transaction deduplication.

    The category is left out on purpose: it can be filled in automatically or
    corrected, so the same bank row may arrive with a different category.
    """
    if hasattr(date_value, "date"):
        normalized_date = date_value.date().isoformat()
    else:
        normalized_date = str(date_value).strip()

    normalized_amount = round(float(amount), 2)
    normalized_type = str(transaction_type or "Expense").strip().title()
    normalized_description = " ".join(str(description or "").split()).lower()
    return (normalized_date, normalized_amount, normalized_type, normalized_description)


def create_user(db, username: str, email: str, password: str) -> int:
    """Persist a new user with a hashed password."""
    password_hash = generate_password_hash(password)
    row = db.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?) RETURNING id",
        (username, email, password_hash),
    ).fetchone()
    db.commit()
    return row[0]


def get_user_by_username(db, username: str) -> User | None:
    """Retrieve a user by username."""
    row = db.execute(
        "SELECT id, username, email, password_hash FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    return User.from_row(row) if row is not None else None


def get_user_by_email(db, email: str) -> User | None:
    """Retrieve a user by email."""
    row = db.execute(
        "SELECT id, username, email, password_hash FROM users WHERE email = ?",
        (email,),
    ).fetchone()
    return User.from_row(row) if row is not None else None


def verify_password(user: User, password: str) -> bool:
    """Validate a password against the stored hash."""
    return check_password_hash(user.password_hash, password)


def imported_signatures(db, user_id: int) -> set[tuple[str, float, str, str]]:
    """Duplicate-check signatures of every transaction the user already has."""
    rows = db.execute("SELECT date, amount, type, description FROM transactions WHERE user_id = ?", (user_id,)).fetchall()
    return {transaction_signature(row["date"], row["amount"], row["type"], row["description"]) for row in rows}


def save_transactions(db, user_id: int, transactions_df, source_file: str | None = None) -> int:
    """Insert cleaned transaction rows into the database without duplicates."""
    existing_keys = imported_signatures(db, user_id)

    new_rows = []
    for row in transactions_df.to_dict("records"):
        transaction_date = row["Date"].date().isoformat() if hasattr(row["Date"], "date") else str(row["Date"])
        transaction_type = str(row.get("Transaction_Type", row.get("Type", "Expense")))
        metadata = {}
        for column in transactions_df.columns:
            if column in {"Date", "Category", "Amount", "Type", "Description", "Transaction_Type", "Year", "Month", "Month_Number", "Day", "Day_Name", "Week", "Quarter", "Weekend"}:
                continue
            value = row[column]
            if pd is not None and hasattr(value, "isna") and value.isna():
                continue
            metadata[column] = value
        metadata_json = json.dumps(metadata) if metadata else None
        payload = (
            transaction_date,
            str(row["Category"]),
            float(row["Amount"]),
            str(row["Type"]),
            str(row["Description"]),
            source_file or "",
            transaction_type,
            metadata.get("account") if isinstance(metadata, dict) and "account" in metadata else None,
            metadata.get("currency") if isinstance(metadata, dict) and "currency" in metadata else None,
            metadata.get("tags") if isinstance(metadata, dict) and "tags" in metadata else None,
            metadata_json,
        )
        signature = transaction_signature(payload[0], payload[2], payload[3], payload[4])
        if signature in existing_keys:
            continue
        existing_keys.add(signature)
        new_rows.append((user_id, *payload))

    # One batch: a 2 MB file is ~30,000 rows, and one statement per row is ~30,000 round trips to Postgres.
    if new_rows:
        db.executemany(
            """
            INSERT INTO transactions (
                user_id, date, category, amount, type, description, source_file,
                transaction_type, account, currency, tags, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            new_rows,
        )
    db.commit()
    return len(new_rows)


def deduplicate_transactions_in_db(db, user_id: int | None = None) -> int:
    """Remove duplicate transactions from the database using a canonical signature."""
    query = "SELECT id, user_id, date, amount, type, description FROM transactions"
    params: tuple[Any, ...] = ()
    if user_id is not None:
        query += " WHERE user_id = ?"
        params = (user_id,)

    rows = db.execute(query, params).fetchall()
    seen: set[tuple[str, float, str, str]] = set()
    duplicate_ids: list[int] = []

    for row in rows:
        signature = transaction_signature(row["date"], row["amount"], row["type"], row["description"])
        if signature in seen:
            duplicate_ids.append(row["id"])
        else:
            seen.add(signature)

    if duplicate_ids:
        placeholders = ",".join("?" for _ in duplicate_ids)
        db.execute(f"DELETE FROM transactions WHERE id IN ({placeholders})", duplicate_ids)
        db.commit()

    return len(duplicate_ids)


def get_user_transactions(db, user_id: int):
    """Fetch transactions for a user sorted by most recent first."""
    return db.execute(
        """
        SELECT id, user_id, date, category, amount, type, description, source_file, created_at
        FROM transactions
        WHERE user_id = ?
        ORDER BY date DESC, id DESC
        """,
        (user_id,),
    ).fetchall()


def get_cached_insights(db, user_id: int) -> dict | None:
    """Return the insights stored for a user, or None if they were never computed."""
    row = db.execute("SELECT payload FROM user_insights WHERE user_id = ?", (user_id,)).fetchone()
    return json.loads(row["payload"]) if row is not None else None


def save_insights(db, user_id: int, insights: dict) -> None:
    """Store a user's computed insights, replacing any previous ones."""
    db.execute(
        """
        INSERT INTO user_insights (user_id, computed_at, payload) VALUES (?, ?, ?)
        ON CONFLICT (user_id) DO UPDATE SET computed_at = excluded.computed_at, payload = excluded.payload
        """,
        (user_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), json.dumps(insights)),
    )
    db.commit()


def get_user_categories(db, user_id: int, limit: int = 20) -> list[str]:
    """The user's most used categories, most used first, without the blank placeholder."""
    rows = db.execute(
        """
        SELECT category FROM transactions WHERE user_id = ? AND category <> 'Uncategorized'
        GROUP BY category ORDER BY COUNT(*) DESC, category LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [row["category"] for row in rows]


def claim_import_token(db, user_id: int, token: str) -> bool:
    """Mark one reviewed import as saved; False if it already was (a double tap or a resubmitted form).

    Not committed here: the caller's save commits the token together with the rows, and a
    second request with the same token waits on the first one's lock, then fails.
    """
    now = datetime.now(timezone.utc)
    # A review form expires after an hour, so day-old tokens can never come back.
    db.execute("DELETE FROM import_tokens WHERE created_at < ?", ((now - timedelta(days=1)).isoformat(timespec="seconds"),))
    try:
        db.execute("INSERT INTO import_tokens (user_id, token, created_at) VALUES (?, ?, ?)", (user_id, token, now.isoformat(timespec="seconds")))
    except IntegrityError:
        db.rollback()
        return False
    return True


def get_category_overrides(db, user_id: int) -> dict[str, str]:
    """The categories this user chose for merchants, by merchant key."""
    rows = db.execute("SELECT merchant_key, category FROM category_overrides WHERE user_id = ?", (user_id,)).fetchall()
    return {row["merchant_key"]: row["category"] for row in rows}


def save_category_overrides(db, user_id: int, overrides: dict[str, str]) -> None:
    """Remember the user's category for each merchant, replacing any earlier choice."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for merchant_key, category in overrides.items():
        db.execute(
            """
            INSERT INTO category_overrides (user_id, merchant_key, category, updated_at) VALUES (?, ?, ?, ?)
            ON CONFLICT (user_id, merchant_key) DO UPDATE SET category = excluded.category, updated_at = excluded.updated_at
            """,
            (user_id, merchant_key, category, now),
        )
    db.commit()


def delete_user(db, user_id: int) -> None:
    """Delete a user and everything stored for them (transactions, insights, category corrections) in one commit."""
    db.execute("DELETE FROM import_tokens WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM category_overrides WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM user_insights WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
