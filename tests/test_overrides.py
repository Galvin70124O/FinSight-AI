import sqlite3

from app import create_app
from models import get_category_overrides, save_category_overrides


def test_an_older_database_gains_the_overrides_table_on_start(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO users (username, email, password_hash) VALUES ('asha', 'asha@example.com', 'x');"
    )
    old.commit()
    old.close()

    create_app({"TESTING": True, "DATABASE_PATH": str(path)})

    conn = sqlite3.connect(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "category_overrides" in tables
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_saving_an_override_again_replaces_it_for_that_user_only(db, user_id):
    other = db.execute("INSERT INTO users (username, email, password_hash) VALUES ('ravi', 'ravi@example.com', 'x') RETURNING id").fetchone()[0]
    save_category_overrides(db, user_id, {"ZOMATO": "Dining", "RAJ MEDICALS": "Healthcare"})
    save_category_overrides(db, other, {"ZOMATO": "Shopping"})

    save_category_overrides(db, user_id, {"ZOMATO": "Groceries"})

    assert get_category_overrides(db, user_id) == {"ZOMATO": "Groceries", "RAJ MEDICALS": "Healthcare"}
    assert get_category_overrides(db, other) == {"ZOMATO": "Shopping"}
