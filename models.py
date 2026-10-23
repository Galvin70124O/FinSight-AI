from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pandas as pd
from werkzeug.security import check_password_hash, generate_password_hash


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


def _normalize_transaction_signature(date_value: Any, category: Any, amount: Any, transaction_type: Any, description: Any) -> tuple[str, str, float, str, str]:
    """Create a stable signature for transaction deduplication."""
    if hasattr(date_value, "date"):
        normalized_date = date_value.date().isoformat()
    else:
        normalized_date = str(date_value).strip()

    normalized_category = str(category or "").strip().lower()
    normalized_amount = round(float(amount), 2)
    normalized_type = str(transaction_type or "Expense").strip().title()
    normalized_description = str(description or "").strip().lower()
    return (normalized_date, normalized_category, normalized_amount, normalized_type, normalized_description)


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


def save_transactions(db, user_id: int, transactions_df, source_file: str | None = None) -> int:
    """Insert cleaned transaction rows into the database without duplicates."""
    existing_rows = db.execute(
        "SELECT date, category, amount, type, description FROM transactions WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    existing_keys = {
        _normalize_transaction_signature(row["date"], row["category"], row["amount"], row["type"], row["description"])
        for row in existing_rows
    }

    count = 0
    for _, row in transactions_df.iterrows():
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
        signature = _normalize_transaction_signature(payload[0], payload[1], payload[2], payload[3], payload[4])
        if signature in existing_keys:
            continue

        db.execute(
            """
            INSERT INTO transactions (
                user_id, date, category, amount, type, description, source_file,
                transaction_type, account, currency, tags, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, *payload),
        )
        existing_keys.add(signature)
        count += 1
    db.commit()
    return count


def deduplicate_transactions_in_db(db, user_id: int | None = None) -> int:
    """Remove duplicate transactions from the database using a canonical signature."""
    query = "SELECT id, user_id, date, category, amount, type, description FROM transactions"
    params: tuple[Any, ...] = ()
    if user_id is not None:
        query += " WHERE user_id = ?"
        params = (user_id,)

    rows = db.execute(query, params).fetchall()
    seen: set[tuple[str, str, float, str, str]] = set()
    duplicate_ids: list[int] = []

    for row in rows:
        signature = _normalize_transaction_signature(row["date"], row["category"], row["amount"], row["type"], row["description"])
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
