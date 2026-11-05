"""Bank statements through the upload page: read, then handed to the existing review and Save."""
import csv
import io
from pathlib import Path

import pytest

from conftest import csrf_token, xls_bytes, xlsx_bytes
from test_import_review import review_form

FIXTURES = Path(__file__).parent / "fixtures" / "statements"


def upload(client, content: bytes, filename: str):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), filename), "csrf_token": csrf_token(client, "/upload")},
        content_type="multipart/form-data",
        follow_redirects=True,
    ).get_data(as_text=True)


def fixture_rows(bank):
    with open(FIXTURES / f"{bank}.csv", encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def saved(db, user_id):
    return [tuple(row) for row in db.execute(
        "SELECT date, amount, type FROM transactions WHERE user_id = ? ORDER BY date, amount", (user_id,))]


@pytest.mark.parametrize("bank, data, name, message", [
    ("hdfc", None, "hdfc.csv",
     "Read 6 transactions from rows 10–17 (HDFC Bank layout). 1 row with no debit or credit was skipped."),
    ("icici", "xlsx", "icici.xlsx",
     "Read 4 transactions from rows 5–9 (ICICI Bank layout). 1 row with no debit or credit was skipped."),
    ("kotak", None, "kotak.csv", "Read 3 transactions from rows 8–10 (Kotak Mahindra Bank layout)."),
])
def test_a_bank_statement_goes_to_the_review_step_and_saves_once(client, db, logged_in, bank, data, name, message):
    content = xlsx_bytes(fixture_rows(bank)) if data == "xlsx" else (FIXTURES / f"{bank}.csv").read_bytes()
    page = upload(client, content, name)
    assert message in page and "Check the categories" in page
    assert saved(db, logged_in) == []  # nothing is saved before Save

    done = client.post("/upload/save", data=review_form(page), follow_redirects=True).get_data(as_text=True)
    assert "Imported" in done
    first = saved(db, logged_in)
    assert first and all(kind in ("Income", "Expense") for *_, kind in first)

    again = upload(client, content, name)  # the existing duplicate check still applies
    assert "Nothing new to import" in again
    assert saved(db, logged_in) == first


def test_an_old_xls_statement_is_read(client, db, logged_in):
    from datetime import date
    rows = [["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"],
            [date(2026, 10, 2), date(2026, 10, 2), "BY TRANSFER-NEFT-SALARY", "", None, 95000.0, 153200.0]]
    page = upload(client, xls_bytes(rows), "sbi.xls")
    assert "Read 1 transaction from rows 2–2 (State Bank of India layout)." in page


def test_a_statement_with_its_own_categories_is_saved_straight_away(client, db, logged_in):
    content = (b"Date,Narration,Debit,Credit,Category\n"
               b"25/10/2026,UPI-BIGBASKET,1240.50,,Groceries\n26/10/2026,NEFT-SALARY,,85000,Income\n")
    page = upload(client, content, "export.csv")
    assert "Imported 2 transactions." in page
    assert saved(db, logged_in) == [("2026-10-25", 1240.5, "Expense"), ("2026-10-26", 85000.0, "Income")]


@pytest.mark.parametrize("name, message", [
    ("statement.xlsm", "This workbook contains macros"),
    ("statement.pdf", "PDF statements can&#39;t be read"),
    ("statement.numbers", "FinSight reads .csv, .xlsx and .xls files, and statement.numbers is not one of them"),
])
def test_files_that_are_not_statements_are_refused_with_a_specific_message(client, db, logged_in, name, message):
    page = upload(client, xlsx_bytes([["Date"]]), name)
    assert message in page
    assert saved(db, logged_in) == []


def test_a_statement_that_needs_checking_is_not_imported(client, db, logged_in):
    page = upload(client, b"Account,XXXX1234\n\nDate,Narration,Debit,Credit\n31/02/2026,Groceries,500,\n", "s.csv")
    assert "Row 4: &#39;31/02/2026&#39; is not a valid date." in page
    assert saved(db, logged_in) == []
