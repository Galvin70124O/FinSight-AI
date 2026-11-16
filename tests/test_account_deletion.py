import io

from conftest import csrf_token, register_and_login
from models import create_goal, dismiss_recurring, save_category_overrides

CSV = b"Date,Category,Amount,Type,Description\n2025-01-02,Food,250,Expense,Lunch\n2025-01-01,Salary,9000,Income,Pay\n"


def rows(db, user_id):
    return {
        table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (user_id,)).fetchone()[0]
        for table, column in (("users", "id"), ("transactions", "user_id"), ("user_insights", "user_id"), ("category_overrides", "user_id"),
                              ("goals", "user_id"), ("recurring_dismissals", "user_id"))
    }


def setup_two_users(app):
    first, second = app.test_client(), app.test_client()
    first_id, second_id = register_and_login(first, "asha"), register_and_login(second, "ravi")
    for client in (first, second):
        client.post("/upload", data={"file": (io.BytesIO(CSV), "tx.csv"), "csrf_token": csrf_token(client, "/upload")},
                    content_type="multipart/form-data")
    return first, first_id, second_id


def delete(client, password="correct-horse-battery", confirm="DELETE"):
    return client.post("/account/delete", data={"password": password, "confirm": confirm, "csrf_token": csrf_token(client, "/upload")},
                       follow_redirects=True).get_data(as_text=True)


def test_deleting_removes_the_account_and_all_its_data_only(app, db):
    client, first_id, second_id = setup_two_users(app)
    for user_id in (first_id, second_id):
        save_category_overrides(db, user_id, {"ZOMATO": "Dining"})
        create_goal(db, user_id, "Goa trip", 60000.0, "2027-06-01")
        dismiss_recurring(db, user_id, "NETFLIX", 30, 64900)
    assert rows(db, first_id) == {"users": 1, "transactions": 2, "user_insights": 1, "category_overrides": 1, "goals": 1, "recurring_dismissals": 1}

    page = delete(client)

    assert "have been deleted" in page
    assert rows(db, first_id) == {"users": 0, "transactions": 0, "user_insights": 0, "category_overrides": 0, "goals": 0, "recurring_dismissals": 0}
    assert rows(db, second_id) == {"users": 1, "transactions": 2, "user_insights": 1, "category_overrides": 1, "goals": 1, "recurring_dismissals": 1}
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_wrong_password_or_missing_confirmation_deletes_nothing(app, db):
    client, first_id, _ = setup_two_users(app)

    assert "nothing was deleted" in delete(client, password="wrong-password-123")
    assert "Nothing was deleted" in delete(client, confirm="delete")
    assert rows(db, first_id)["users"] == 1


def test_deleting_needs_a_signed_in_user(client):
    response = client.post("/account/delete", data={"password": "x", "confirm": "DELETE", "csrf_token": csrf_token(client, "/login")})
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_a_device_still_signed_in_after_the_account_is_deleted_is_signed_out(app):
    laptop = app.test_client()
    register_and_login(laptop)
    phones = [app.test_client() for _ in range(3)]  # one per request: the first redirect signs a device out
    for phone in phones:
        phone.post("/login", data={"username": "asha", "password": "correct-horse-battery", "csrf_token": csrf_token(phone, "/login")})
    token = csrf_token(phones[2], "/dashboard")  # taken before the delete, as an open page would have it
    assert "have been deleted" in delete(laptop)

    responses = phones[0].get("/"), phones[1].get("/dashboard"), phones[2].post("/demo-data", data={"csrf_token": token})
    for phone, response in zip(phones, responses):
        assert response.status_code == 302 and response.headers["Location"].endswith("/login")
        with phone.session_transaction() as session:
            assert "user_id" not in session
    assert "This account was deleted, so you were signed out." in phones[0].get("/login").get_data(as_text=True)
