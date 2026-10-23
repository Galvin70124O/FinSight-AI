import sqlite3

from app import create_app
from conftest import csrf_token, register_and_login


def count(db, user_id):
    return db.execute("SELECT COUNT(*) FROM transactions WHERE user_id = ?", (user_id,)).fetchone()[0]


def test_fresh_database_starts_empty(tmp_path):
    db_path = tmp_path / "fresh.db"
    app = create_app({"TESTING": True, "DATABASE_PATH": str(db_path)})

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert app.test_client().get("/").status_code == 200


def test_empty_dashboard_offers_demo_data(client, logged_in):
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Load demo data" in page.get_data(as_text=True)


def test_demo_data_loads_into_current_user_only(app, db):
    first, second = app.test_client(), app.test_client()
    first_id = register_and_login(first, "asha")
    second_id = register_and_login(second, "ravi")

    page = second.post("/demo-data", data={"csrf_token": csrf_token(second, "/dashboard")}, follow_redirects=True)

    assert "demo transaction(s)" in page.get_data(as_text=True)
    assert count(db, second_id) > 100
    assert count(db, first_id) == 0
    assert "Load demo data" not in page.get_data(as_text=True)

    # Loading twice does not duplicate rows.
    before = count(db, second_id)
    second.post("/demo-data", data={"csrf_token": csrf_token(second, "/dashboard")})
    assert count(db, second_id) == before
