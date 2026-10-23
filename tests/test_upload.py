import io
import os

from conftest import csrf_token

VALID_CSV = (
    b"Date,Category,Amount,Type,Description\n"
    b"2025-01-01,Salary,85000,Income,January salary\n"
    b"2025-01-02,Food,250,Expense,Lunch\n"
)


def upload(client, content: bytes, filename: str = "tx.csv"):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), filename), "csrf_token": csrf_token(client, "/upload")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def count(db):
    return db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


def test_valid_csv_is_imported_and_not_kept_on_disk(app, client, db, logged_in):
    page = upload(client, VALID_CSV).get_data(as_text=True)

    assert "Imported 2 transaction(s)" in page
    assert count(db) == 2
    assert not os.path.exists(os.path.join(app.root_path, "uploads", "tx.csv"))


def test_non_csv_extension_is_rejected(client, db, logged_in):
    page = upload(client, VALID_CSV, "tx.xlsx").get_data(as_text=True)
    assert "Only CSV files are supported." in page
    assert count(db) == 0


def test_missing_columns_are_reported(client, db, logged_in):
    page = upload(client, b"Date,Amount\n2025-01-02,250\n").get_data(as_text=True)
    assert "Missing required columns: Category, Type, Description" in page
    assert count(db) == 0


def test_file_over_size_limit_is_rejected(client, db, logged_in):
    big = VALID_CSV + b"2025-01-03,Food,10,Expense," + b"x" * (3 * 1024 * 1024) + b"\n"
    page = upload(client, big).get_data(as_text=True)
    assert "too large" in page
    assert count(db) == 0


def test_unreadable_file_shows_friendly_message(client, db, logged_in):
    page = upload(client, b"\xff\xfe\x00garbage\x00\x81").get_data(as_text=True)
    assert "could not be read as a CSV" in page or "Missing required columns" in page
    assert "Traceback" not in page and "Error tokenizing" not in page
    assert count(db) == 0
