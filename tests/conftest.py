import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# app.py builds a module-level app on import, so point it at a throwaway
# database before anything imports it. Tests never touch instance/.
os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(prefix="finsight-tests-"), "import.db")

from app import create_app  # noqa: E402
from database import get_db  # noqa: E402


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": str(tmp_path / "test.db"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "TRAINED_MODELS_DIR": str(tmp_path / "models"),
        }
    )
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()


@pytest.fixture
def user_id(db):
    from models import create_user

    return create_user(db, "asha", "asha@example.com", "secret123")
