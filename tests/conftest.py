import io
import os
import re
import sqlite3
import sys
import tempfile
import uuid
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


@pytest.fixture(scope="module")
def postgres_url():
    pgserver = pytest.importorskip("pgserver")  # embedded Postgres, a dev requirement
    with pgserver.get_server(tempfile.mkdtemp(prefix="finsight-pg-"), cleanup_mode="stop") as server:
        yield server.get_uri()


@pytest.fixture(params=["sqlite", "postgres"])
def engine_app(request, tmp_path):
    config = {"TESTING": True, "DATABASE_PATH": str(tmp_path / "test.db")}
    if request.param == "postgres":
        config["DATABASE_URL"] = request.getfixturevalue("postgres_url")
    return create_app(config)


@pytest.fixture
def engine_db(engine_app):
    """A database connection on SQLite, then on embedded Postgres: for SQL the two could read differently."""
    from database import get_db

    with engine_app.app_context():
        yield get_db()


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


def own_connection(app):
    """A connection kept out of flask.g, on either engine, for a `db` fixture: the test's requests open their own,
    so they never share it (or a cached CSRF token). Use as `yield from own_connection(app)`."""
    from flask import g

    from database import get_db

    with app.app_context():
        conn = get_db()
        g.pop("db")  # close_db only closes what is in g, so this one outlives the context
    yield conn
    conn.close()


def sign_in(client, db) -> int:
    """A new user with a unique name, signed in through the session: the embedded Postgres is shared by every
    test in a file, and signing up through /register would also count towards the per-address sign-up limit."""
    from models import create_user

    name = f"u{uuid.uuid4().hex[:12]}"
    user_id = create_user(db, name, f"{name}@example.com", "correct-horse-battery")
    with client.session_transaction() as session:
        session["user_id"], session["username"] = user_id, name
    return user_id


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
