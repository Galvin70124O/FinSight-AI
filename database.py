import sqlite3
from flask import g, current_app


def get_db() -> sqlite3.Connection:
    """Create or return a database connection for the current request."""
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(_error: Exception | None = None) -> None:
    """Close the current database connection."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    """Create application tables if they do not already exist."""
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            category TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL,
            description TEXT NOT NULL,
            source_file TEXT,
            transaction_type TEXT,
            account TEXT,
            currency TEXT,
            tags TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id)
        );

        CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date);
        """
    )
    db.commit()

    for column_name, column_def in {
        "transaction_type": "TEXT",
        "account": "TEXT",
        "currency": "TEXT",
        "tags": "TEXT",
        "metadata": "TEXT",
    }.items():
        columns = {row[1] for row in db.execute("PRAGMA table_info(transactions)")}
        if column_name not in columns:
            db.execute(f"ALTER TABLE transactions ADD COLUMN {column_name} {column_def}")
    db.commit()
