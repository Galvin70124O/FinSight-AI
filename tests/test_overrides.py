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


def test_an_older_database_gains_merchant_keys_and_their_index_on_start(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, date TEXT NOT NULL,"
        " category TEXT NOT NULL, amount REAL NOT NULL, type TEXT NOT NULL, description TEXT NOT NULL, source_file TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "INSERT INTO users (username, email, password_hash) VALUES ('asha', 'asha@example.com', 'x');"
        "INSERT INTO transactions (user_id, date, category, amount, type, description)"
        " VALUES (1, '2026-01-01', 'Dining', 100, 'Expense', 'UPI/ZOMATO LTD/412345678901'), (1, '2026-01-02', 'Other', 5, 'Expense', '412345678902');"
    )
    old.commit()
    old.close()

    create_app({"TESTING": True, "DATABASE_PATH": str(path)})

    conn = sqlite3.connect(path)
    assert [r[0] for r in conn.execute("SELECT merchant_key FROM transactions ORDER BY id")] == ["ZOMATO", ""]
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(transactions)")}
    assert "idx_transactions_user_merchant" in indexes
    conn.close()


def test_saved_rows_get_their_merchant_key(db, user_id):
    import pandas as pd

    from models import save_transactions

    save_transactions(db, user_id, pd.DataFrame([{"Date": "2026-01-01", "Category": "Dining", "Amount": 100.0, "Type": "Expense",
                                                   "Description": "UPI-SWIGGY-swiggy@icici-ICIC0000001-412345678901-Order"}]))
    assert db.execute("SELECT merchant_key FROM transactions").fetchone()[0] == "SWIGGY"


def test_old_demo_categories_are_mapped_once_and_later_custom_ones_stay(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, email TEXT NOT NULL UNIQUE,"
        " password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, date TEXT NOT NULL,"
        " category TEXT NOT NULL, amount REAL NOT NULL, type TEXT NOT NULL, description TEXT NOT NULL, source_file TEXT,"
        " created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE category_overrides (user_id INTEGER NOT NULL, merchant_key TEXT NOT NULL, category TEXT NOT NULL,"
        " updated_at TEXT NOT NULL, PRIMARY KEY (user_id, merchant_key));"
        "INSERT INTO users (username, email, password_hash) VALUES ('asha', 'asha@example.com', 'x');"
        "INSERT INTO transactions (user_id, date, category, amount, type, description) VALUES"
        " (1, '2026-01-01', 'Salary', 85000, 'Income', 'Monthly salary'), (1, '2026-01-02', 'Freelance', 9000, 'Income', 'Project'),"
        " (1, '2026-01-03', 'Bills', 800, 'Expense', 'Mobile bill'), (1, '2026-01-04', 'Pet care', 600, 'Expense', 'Vet');"
        "INSERT INTO category_overrides VALUES (1, 'ACME', 'Salary', '2026-01-01');"
    )
    old.commit()
    old.close()

    create_app({"TESTING": True, "DATABASE_PATH": str(path)})
    conn = sqlite3.connect(path)
    assert [r[0] for r in conn.execute("SELECT category FROM transactions ORDER BY id")] == ["Income", "Income", "Utilities", "Pet care"]
    assert conn.execute("SELECT category FROM category_overrides").fetchone()[0] == "Income"

    # A user's own "Salary" imported after the step is a custom category and stays on later starts.
    conn.execute("INSERT INTO transactions (user_id, date, category, amount, type, description, merchant_key)"
                 " VALUES (1, '2026-02-01', 'Salary', 85000, 'Income', 'Pay', 'PAY')")
    conn.commit()
    conn.close()
    create_app({"TESTING": True, "DATABASE_PATH": str(path)})
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT category FROM transactions WHERE date = '2026-02-01'").fetchone()[0] == "Salary"
    assert conn.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 1
    conn.close()


def test_the_sample_data_uses_only_the_one_category_list():
    import pandas as pd

    from ml.categorise import CATEGORIES

    assert set(pd.read_csv("dataset/sample_transactions.csv")["Category"]) <= set(CATEGORIES)
