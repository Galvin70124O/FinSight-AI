"""Database helpers: SQLite locally, Postgres when DATABASE_URL is set.

Queries are written once with SQLite's ``?`` placeholders. For Postgres a thin
wrapper swaps them for ``%s`` and returns rows that can be read by column name
or by position, like ``sqlite3.Row``.
"""

import sqlite3

from datetime import datetime, timezone

from flask import current_app, g

try:
    import psycopg
except ImportError:  # psycopg is only needed when DATABASE_URL is set
    psycopg = None

IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError,)
if psycopg is not None:
    IntegrityError += (psycopg.IntegrityError,)

SQLITE_SCHEMA = """
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
    merchant_key TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date);

CREATE TABLE IF NOT EXISTS user_insights (
    user_id INTEGER PRIMARY KEY,
    computed_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS category_overrides (
    user_id INTEGER NOT NULL,
    merchant_key TEXT NOT NULL,
    category TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, merchant_key),
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS import_tokens (
    user_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, token),
    FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    date TEXT NOT NULL,
    category TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL,
    source_file TEXT,
    transaction_type TEXT,
    account TEXT,
    currency TEXT,
    tags TEXT,
    metadata TEXT,
    merchant_key TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date);

CREATE TABLE IF NOT EXISTS user_insights (
    user_id INTEGER PRIMARY KEY REFERENCES users (id),
    computed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_overrides (
    user_id INTEGER NOT NULL REFERENCES users (id),
    merchant_key TEXT NOT NULL,
    category TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, merchant_key)
);

CREATE TABLE IF NOT EXISTS import_tokens (
    user_id INTEGER NOT NULL REFERENCES users (id),
    token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, token)
);

ALTER TABLE transactions ADD COLUMN IF NOT EXISTS merchant_key TEXT;

CREATE TABLE IF NOT EXISTS migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


class _PgRow(dict):
    """Dict row that also supports positional access, like sqlite3.Row."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _pg_row_factory(cursor):
    names = [column.name for column in cursor.description or []]
    return lambda values: _PgRow(zip(names, values))


class _PostgresConnection:
    """Expose the small part of the sqlite3 connection API the app uses."""

    def __init__(self, url: str) -> None:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        self._conn = psycopg.connect(url)

    def execute(self, sql: str, params=()):
        cursor = self._conn.cursor(row_factory=_pg_row_factory)
        cursor.execute(sql.replace("?", "%s"), tuple(params))
        return cursor

    def executemany(self, sql: str, params_seq) -> None:
        # psycopg 3 sends the whole batch in pipeline mode, not one round trip per row.
        with self._conn.cursor() as cursor:
            cursor.executemany(sql.replace("?", "%s"), [tuple(params) for params in params_seq])

    def executescript(self, script: str) -> None:
        for statement in script.split(";"):
            if statement.strip():
                self._conn.execute(statement)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()


def is_postgres() -> bool:
    return current_app.config.get("DATABASE_URL", "").startswith(("postgres://", "postgresql://"))


def get_db():
    """Create or return a database connection for the current request."""
    if "db" not in g:
        if is_postgres():
            if psycopg is None:
                raise RuntimeError("DATABASE_URL points at Postgres but psycopg is not installed.")
            g.db = _PostgresConnection(current_app.config["DATABASE_URL"])
        else:
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


# The demo data and the first imports used these; merchant_rules.json is now the one list of categories.
OLD_CATEGORIES = {"Salary": "Income", "Freelance": "Income", "Bills": "Utilities"}


def init_db() -> None:
    """Create application tables if they do not already exist, then run the one-time data steps."""
    db = get_db()
    if is_postgres():
        db.executescript(POSTGRES_SCHEMA)
    else:
        db.executescript(SQLITE_SCHEMA)
        # Older local databases were created before these columns existed.
        columns = {row[1] for row in db.execute("PRAGMA table_info(transactions)")}
        for column_name in ("transaction_type", "account", "currency", "tags", "metadata", "merchant_key"):
            if column_name not in columns:
                db.execute(f"ALTER TABLE transactions ADD COLUMN {column_name} TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user_merchant ON transactions(user_id, merchant_key)")
    db.commit()
    backfill_merchant_keys(db)
    _once(db, "2026-11-05 one category list", _map_old_categories)


def backfill_merchant_keys(db) -> int:
    """Give rows saved before the merchant_key column its value. Only empty (NULL) keys are filled, so this
    is a no-op after the first start; after changing merchant_key(), set the column to NULL to recompute."""
    from ml.categorise import merchant_key  # here, not at the top: it loads the model

    rows = db.execute("SELECT id, description FROM transactions WHERE merchant_key IS NULL").fetchall()
    if rows:
        db.executemany("UPDATE transactions SET merchant_key = ? WHERE id = ?", [(merchant_key(row["description"]), row["id"]) for row in rows])
        db.commit()
    return len(rows)


def _once(db, name: str, step) -> None:
    """Run a data step exactly once per database. A second worker starting at the same moment waits on the
    first one's insert, then finds the name taken and skips."""
    try:
        db.execute("INSERT INTO migrations (name, applied_at) VALUES (?, ?)", (name, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    except IntegrityError:
        db.rollback()
        return
    step(db)
    db.commit()


def _map_old_categories(db) -> None:
    """Existing rows and corrections that use the old demo names move to the one list. Categories users
    choose later, including these names from their own files, stay as they are."""
    for old, new in OLD_CATEGORIES.items():
        db.execute("UPDATE transactions SET category = ? WHERE category = ?", (new, old))
        db.execute("UPDATE category_overrides SET category = ? WHERE category = ?", (new, old))
