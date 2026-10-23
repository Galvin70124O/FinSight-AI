import io

import blueprints.main as main_module
from conftest import csrf_token, register_and_login

CSV = (
    "Date,Category,Amount,Type,Description\n"
    "2025-01-01,Salary,80000,Income,Salary\n2025-01-05,Food,3000,Expense,Groceries\n"
    "2025-02-01,Salary,80000,Income,Salary\n2025-02-05,Food,3500,Expense,Groceries\n"
    "2025-03-01,Salary,80000,Income,Salary\n2025-03-05,Food,3200,Expense,Groceries\n"
).encode()


def upload(client):
    client.post(
        "/upload",
        data={"file": (io.BytesIO(CSV), "tx.csv"), "csrf_token": csrf_token(client, "/upload")},
        content_type="multipart/form-data",
    )


def test_insights_are_computed_on_upload_not_on_every_dashboard_load(client, db, logged_in, monkeypatch):
    upload(client)
    assert db.execute("SELECT COUNT(*) FROM user_insights WHERE user_id = ?", (logged_in,)).fetchone()[0] == 1

    def fail(_df):
        raise AssertionError("dashboard recomputed insights")

    monkeypatch.setattr(main_module, "compute_insights", fail)
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Expected spending in Apr 2025" in page


def test_insights_are_per_user(app, db):
    first, second = app.test_client(), app.test_client()
    first_id = register_and_login(first, "asha")
    second_id = register_and_login(second, "ravi")
    upload(first)

    assert "Expected spending" in first.get("/dashboard").get_data(as_text=True)
    second_page = second.get("/dashboard").get_data(as_text=True)
    assert "Expected spending" not in second_page
    assert "Load demo data" in second_page

    rows = {row["user_id"]: row["payload"] for row in db.execute("SELECT user_id, payload FROM user_insights")}
    assert '"forecast": null' in rows[second_id]
    assert '"forecast": null' not in rows[first_id]


def test_no_model_files_are_written(client, logged_in, tmp_path):
    upload(client)
    assert not list(tmp_path.rglob("*.pkl"))


def test_dashboard_without_income_explains_missing_health_score(client, logged_in):
    csv = b"Date,Category,Amount,Type,Description\n2025-01-05,Food,3000,Expense,Groceries\n"
    client.post(
        "/upload",
        data={"file": (io.BytesIO(csv), "tx.csv"), "csrf_token": csrf_token(client, "/upload")},
        content_type="multipart/form-data",
    )
    page = client.get("/dashboard").get_data(as_text=True)
    assert "Add income transactions to see your health score." in page
