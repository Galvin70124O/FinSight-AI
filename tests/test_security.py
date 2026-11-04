import io

import pytest

import config
from conftest import csrf_token


def test_post_without_csrf_token_is_rejected(client, db):
    client.post(
        "/register",
        data={"username": "mallory", "email": "m@example.com", "password": "correct-horse-battery", "confirm_password": "correct-horse-battery"},
    )
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_upload_without_csrf_token_imports_nothing(client, db, logged_in):
    csv = b"Date,Category,Amount,Type,Description\n2025-01-02,Food,250,Expense,Lunch\n"
    client.post("/upload", data={"file": (io.BytesIO(csv), "tx.csv")}, content_type="multipart/form-data")
    assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_logout_requires_post(client, logged_in):
    assert client.get("/logout").status_code == 405

    client.post("/logout", data={"csrf_token": csrf_token(client, "/dashboard")})
    with client.session_transaction() as session:
        assert "user_id" not in session


def test_secret_key_required_in_production(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config._secret_key()


def test_secret_key_read_from_environment(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "from-env")
    monkeypatch.setattr(config, "IS_PRODUCTION", True)
    assert config._secret_key() == "from-env"


def register(client, password):
    return client.post(
        "/register",
        data={"username": "nisha", "email": "nisha@example.com", "password": password,
              "confirm_password": password, "csrf_token": csrf_token(client, "/register")},
        follow_redirects=True,
    ).get_data(as_text=True)


def test_new_passwords_need_twelve_characters(client, db):
    page = register(client, "elevenchars")  # 11
    assert "at least 12 characters" in page
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

    register(client, "twelve chars")  # 12, spaces allowed
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_existing_short_passwords_still_sign_in(client, db, user_id):
    # user_id was created directly with a 9-character password, as under the old rule.
    page = client.post(
        "/login",
        data={"username": "asha", "password": "secret123", "csrf_token": csrf_token(client, "/login")},
        follow_redirects=True,
    ).get_data(as_text=True)
    assert "Incorrect username or password" not in page
    with client.session_transaction() as session:
        assert session["user_id"] == user_id


@pytest.mark.parametrize("path", ["/upload/save", "/account/delete", "/logout"])
def test_an_out_of_date_form_on_a_post_only_address_lands_on_a_real_page(client, logged_in, path):
    # The CSRF check fails (no token); the redirect must not be a GET to the POST-only address (a 405).
    response = client.post(path, data={}, follow_redirects=True)
    assert response.status_code == 200
