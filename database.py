"""Database helpers: SQLite locally, Postgres when DATABASE_URL is set.

Queries are written once with SQLite's ``?`` placeholders. For Postgres a thin
wrapper swaps them for ``%s`` and returns rows that can be read by column name
or by position, like ``sqlite3.Row``.
"""

import sqlite3

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_transactions_user_id ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_user_date ON transactions(user_id, date);

CREATE TABLE IF NOT EXISTS user_insights (
    user_id INTEGER PRIMARY KEY REFERENCES users (id),
    computed_at TEXT NOT NULL,
    payload TEXT NOT NULL
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


def init_db() -> None:
    """Create application tables if they do not already exist."""
    db = get_db()
    if is_postgres():
        db.executescript(POSTGRES_SCHEMA)
        db.commit()
        return

    db.executescript(SQLITE_SCHEMA)
    db.commit()

    # Older local databases were created before these columns existed.
    columns = {row[1] for row in db.execute("PRAGMA table_info(transactions)")}
    for column_name in ("transaction_type", "account", "currency", "tags", "metadata"):
        if column_name not in columns:
            db.execute(f"ALTER TABLE transactions ADD COLUMN {column_name} TEXT")
    db.commit()
