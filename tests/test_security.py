import io

import pytest

import config
from conftest import csrf_token


def test_post_without_csrf_token_is_rejected(client, db):
    client.post(
        "/register",
        data={"username": "mallory", "email": "m@example.com", "password": "secret123", "confirm_password": "secret123"},
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
