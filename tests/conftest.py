import io
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# app.py builds a module-level app on import, so point it at a throwaway
# database before anything imports it. Tests never touch instance/.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="finsight-tests-"), "import.db")

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": str(tmp_path / "test.db"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        }
    )
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    # A separate connection: keeping an app context pushed here would make
    # every test request share one flask.g (and one cached CSRF token).
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    yield conn
    conn.close()


@pytest.fixture
def user_id(db):
    from models import create_user

    return create_user(db, "asha", "asha@example.com", "secret123")


def csrf_token(client, path):
    """Fetch a page and pull the CSRF token out of its form."""
    html = client.get(path).get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, f"no CSRF token on {path}"
    return match.group(1)


def register_and_login(client, username="asha", password="correct-horse-battery"):
    client.post(
        "/register",
        data={
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
            "confirm_password": password,
            "csrf_token": csrf_token(client, "/register"),
        },
    )
    client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": csrf_token(client, "/login")},
    )
    with client.session_transaction() as session:
        assert "user_id" in session, "login failed"
        return session["user_id"]


@pytest.fixture
def logged_in(client):
    return register_and_login(client)


def xlsx_bytes(rows) -> bytes:
    """An .xlsx workbook built in memory, one list per row (None leaves a cell blank)."""
    from openpyxl import Workbook

    book = Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def xls_bytes(rows) -> bytes:
    """An old-style .xls workbook built in memory; datetime values become real Excel date cells."""
    from datetime import date

    import xlwt

    book = xlwt.Workbook()
    sheet = book.add_sheet("Statement")
    date_style = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if isinstance(value, date):
                sheet.write(r, c, value, date_style)
            elif value is not None:
                sheet.write(r, c, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
