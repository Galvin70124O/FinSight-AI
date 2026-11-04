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


def _add_rows(db, user_id, n, description="UPI/ZOMATO LTD/412345678901", category="Uncategorized"):
    ids = []
    for i in range(n):
        ids.append(db.execute(
            "INSERT INTO transactions (user_id, date, category, amount, type, description) VALUES (?, ?, ?, ?, 'Expense', ?) RETURNING id",
            (user_id, f"2026-01-{i % 28 + 1:02d}", category, 100 + i, f"{description}{i}"),
        ).fetchone()[0])
    db.commit()
    return ids


def test_recategorise_changes_only_this_users_rows_and_remembers_on_request(db, user_id):
    from models import recategorise

    other = db.execute("INSERT INTO users (username, email, password_hash) VALUES ('ravi', 'ravi@example.com', 'x') RETURNING id").fetchone()[0]
    mine, theirs = _add_rows(db, user_id, 3), _add_rows(db, other, 2)

    # Another user's ids slipped into the list are ignored.
    assert recategorise(db, user_id, mine[:2] + theirs, "Dining") == 2
    assert recategorise(db, user_id, mine[2:], "Groceries", remember_as="ZOMATO") == 1

    categories = dict(db.execute("SELECT id, category FROM transactions").fetchall())
    assert [categories[i] for i in mine] == ["Dining", "Dining", "Groceries"]
    assert [categories[i] for i in theirs] == ["Uncategorized", "Uncategorized"]
    assert get_category_overrides(db, user_id) == {"ZOMATO": "Groceries"}
    assert get_category_overrides(db, other) == {}


def test_recategorise_handles_more_rows_than_one_query_can_list(db, user_id):
    from models import recategorise

    ids = _add_rows(db, user_id, 1200)
    assert recategorise(db, user_id, ids, "Dining") == 1200
    assert db.execute("SELECT COUNT(*) FROM transactions WHERE category = 'Dining'").fetchone()[0] == 1200
