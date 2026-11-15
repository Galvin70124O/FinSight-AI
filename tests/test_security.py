import io
import re

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


SIGNED_OUT = ["/", "/login", "/register", "/privacy", "/terms", "/no-such-page"]
SIGNED_IN = ["/dashboard", "/categories", "/upload"]
REVIEW_CSV = b"Date,Amount,Type,Description\n2026-10-03,486,Expense,UPI/ZOMATO LTD/412345678902\n"


def test_every_page_sends_the_security_headers_and_runs_no_inline_script(app, client, logged_in):
    client.post("/demo-data", data={"csrf_token": csrf_token(client, "/dashboard")})  # charts, bars and meters on the overview
    review = client.post("/upload", data={"file": (io.BytesIO(REVIEW_CSV), "s.csv"), "csrf_token": csrf_token(client, "/upload")},
                         content_type="multipart/form-data")
    responses = [(path, app.test_client().get(path)) for path in SIGNED_OUT]
    responses += [(path, client.get(path)) for path in SIGNED_IN] + [("/upload (review)", review)]
    for path, response in responses:
        assert response.headers.get("X-Frame-Options") == "DENY", path
        assert response.headers.get("X-Content-Type-Options") == "nosniff", path
        assert response.headers.get("Referrer-Policy") == "same-origin", path
        policy = response.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in policy and "frame-ancestors 'none'" in policy and "unsafe-eval" not in policy, path
        assert "script-src" not in policy or "script-src 'self';" in policy + ";", path
        # What the policy blocks: a <script> with code in it. JSON data blocks are never run, so they stay.
        for tag in re.findall(r"<script\b[^>]*>", response.get_data(as_text=True)):
            assert "src=" in tag or 'type="application/json"' in tag, (path, tag)


def test_each_sign_in_attempt_logs_the_address_it_is_counted_under_but_never_the_username(app, client, db, user_id, caplog):
    app.config["IS_PRODUCTION"] = True
    caplog.set_level("DEBUG", logger=app.logger.name)
    for password in ("wrong", "secret123"):
        client.post("/login", data={"username": "asha", "password": password, "csrf_token": csrf_token(client, "/login")},
                    headers={"CF-Connecting-IP": "203.0.113.7"}, environ_base={"REMOTE_ADDR": "10.0.0.1"})
    logged = [record.getMessage() for record in caplog.records]
    assert [line for line in logged if "Sign-in attempt" in line] == ["Sign-in attempt from 203.0.113.7"] * 2
    assert not any("asha" in line for line in logged)
