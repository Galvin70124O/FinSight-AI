"""Bank statements through the upload page: read, then handed to the existing review and Save. When the reader
needs help, the column screen asks which column is which and remembers the answer."""
import csv
import io
import random
import re
from pathlib import Path
from urllib.parse import urlencode

import pytest

from conftest import csrf_token, register_and_login, xls_bytes, xlsx_bytes
from ml.statement_parser import locate, read_grid
from models import delete_user, get_column_mapping, save_column_mapping
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


def test_a_statement_that_needs_checking_shows_its_columns_and_saves_nothing(client, db, logged_in):
    content = b"Account,XXXX1234\n\nDate,Narration,Debit,Credit\n31/02/2026,Groceries,500,\n"
    page = upload(client, content, "s.csv")
    assert "Match the columns" in page and "Row 4: &#39;31/02/2026&#39; is not a valid date." in page
    assert saved(db, logged_in) == [] and get_column_mapping(db, logged_in, key_of(content)) is None


UNKNOWN = b"My bank export\n\nWhen,What,Money out,Money in\n25/10/2026,Groceries,500,\n26/10/2026,Salary,,9000\n"
UNKNOWN_ROLES = ["date", "description", "debit", "credit"]
AMBIGUOUS = b"Date,Description,Debit,Credit,Balance\n03/04/2026,Groceries,500,,100\n05/04/2026,Salary,,9000,200\n"
AMBIGUOUS_ROLES = ["date", "description", "debit", "credit", "ignore"]


def key_of(content: bytes) -> str:
    return locate(read_grid(content, "s.csv")).key


def mapping_form(page: str) -> dict[str, str]:
    """The column screen's fields as a browser sends them: hidden fields, each column's chosen role, a ticked radio."""
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
    for name, body in re.findall(r'<select[^>]*name="(role-\d+)"[^>]*>(.*?)</select>', page, re.S):
        fields[name] = re.search(r'<option value="([^"]*)" selected>', body).group(1)
    fields.update(re.findall(r'<input type="radio" name="(date_order)" value="([^"]*)"[^>]*checked', page))
    return fields


def pick(page: str, roles: list[str], **extra) -> dict[str, str]:
    return {**mapping_form(page), **{f"role-{i}": role for i, role in enumerate(roles)}, **extra}


def test_columns_picked_on_the_screen_are_read_and_remembered_for_next_time(client, db, logged_in):
    page = upload(client, UNKNOWN, "s.csv")
    assert "Match the columns" in page and "Pick the Date column." in page and 'action="/upload/columns"' in page
    assert get_column_mapping(db, logged_in, key_of(UNKNOWN)) is None

    review = client.post("/upload/columns", data=pick(page, UNKNOWN_ROLES), follow_redirects=True).get_data(as_text=True)
    assert "Read 2 transactions from rows 4–5 (Generic layout)." in review and "Check the categories" in review
    assert get_column_mapping(db, logged_in, key_of(UNKNOWN)) == (UNKNOWN_ROLES, None)
    assert "Match the columns" not in upload(client, UNKNOWN, "s.csv")  # remembered: no screen the second time


def test_wrong_picks_come_back_with_the_problem_and_nothing_is_remembered(client, db, logged_in):
    page = upload(client, UNKNOWN, "s.csv")
    again = client.post("/upload/columns", data=pick(page, ["date", "description", "debit", "ignore"])).get_data(as_text=True)
    assert "Pick the Credit column too, or one Amount column instead of Debit." in again
    assert [mapping_form(again)[f"role-{i}"] for i in range(4)] == ["date", "description", "debit", "ignore"]  # kept, not re-guessed
    assert get_column_mapping(db, logged_in, key_of(UNKNOWN)) is None and saved(db, logged_in) == []


def test_ambiguous_dates_are_asked_and_the_answer_is_remembered(client, db, logged_in):
    page = upload(client, AMBIGUOUS, "s.csv")
    assert "Dates such as 03/04/2026 could be read two ways." in page
    assert "3 Apr 2026" in page and "4 Mar 2026" in page and "date_order" not in mapping_form(page)  # nothing chosen for you
    review = client.post("/upload/columns", data=pick(page, AMBIGUOUS_ROLES, date_order="month"),
                         follow_redirects=True).get_data(as_text=True)
    assert "Read 2 transactions" in review and "4 Mar 2026" in review and "4 May 2026" in review
    assert get_column_mapping(db, logged_in, key_of(AMBIGUOUS)) == (AMBIGUOUS_ROLES, "month")


def test_a_remembered_mapping_that_no_longer_reads_comes_back_preselected(client, db, logged_in):
    save_column_mapping(db, logged_in, key_of(UNKNOWN), ["date", "description", "debit", "ignore"], None)
    page = upload(client, UNKNOWN, "s.csv")
    assert "Pick the Credit column too" in page
    assert [mapping_form(page)[f"role-{i}"] for i in range(4)] == ["date", "description", "debit", "ignore"]


def test_the_column_screen_only_takes_back_its_own_signed_rows(app, db):
    asha, ravi = app.test_client(), app.test_client()
    register_and_login(asha, "asha")
    ravi_id = register_and_login(ravi, "ravi")
    form = pick(upload(asha, UNKNOWN, "s.csv"), UNKNOWN_ROLES, csrf_token=csrf_token(ravi, "/upload"))
    for payload in (form["payload"], form["payload"][:-6] + "AAAAAA"):  # another user's, then a changed one
        page = ravi.post("/upload/columns", data={**form, "payload": payload}, follow_redirects=True).get_data(as_text=True)
        assert "expired or could not be read, so nothing was imported" in page
    assert get_column_mapping(db, ravi_id, key_of(UNKNOWN)) is None


def test_text_from_the_file_is_shown_as_text_on_the_column_screen(client, db, logged_in):
    page = upload(client, b"When,<b>What</b>,Money out,Money in\n25/10/2026,<script>alert(1)</script>,500,\n", "s.csv")
    assert "Match the columns" in page and "<script>alert(1)</script>" not in page and "<b>What</b>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "&lt;b&gt;What&lt;/b&gt;" in page


def test_a_file_too_big_for_the_column_screen_says_what_to_do(app, client, db, logged_in):
    app.config["MAX_CONTENT_LENGTH"] = 4096 + 100  # leaves no room for the rows in the form
    page = upload(client, UNKNOWN, "s.csv")
    assert "Match the columns" not in page and "Upload one month of it first" in page


def test_the_largest_accepted_spreadsheet_round_trips_through_the_column_screen(app, client, db, logged_in):
    """About 37,000 rows of an unknown layout in a 2 MB .xlsx, with a unique reference on every row: more text than
    a 2 MB CSV holds, and the column screen sends all of it back."""
    rng = random.Random(1)
    rows, balance = [["When", "What", "Ref", "Money out", "Money in", "Left"]], 9_000_000.0
    for _ in range(37_000):
        out = round(rng.uniform(20, 5000), 2)
        balance -= out
        rows.append([f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2025",
                     f"UPI/SHOP {rng.randint(1, 300)}/{rng.randint(10 ** 11, 10 ** 12 - 1)}/Paid via PhonePe",
                     str(rng.randint(10 ** 11, 10 ** 12 - 1)), out, None, round(balance, 2)])
    content = xlsx_bytes(rows)
    assert 1_900_000 < len(content) < app.config["MAX_CONTENT_LENGTH"] - 10_000

    page = upload(client, content, "big.xlsx")
    assert "Match the columns" in page
    assert "enctype" not in page.split('action="/upload/columns"')[1].split(">")[0]  # URL-encoded: see the template
    form = pick(page, ["date", "description", "ignore", "debit", "credit", "ignore"])
    request_size = len(urlencode(form))
    print(f"\n{len(content):,} B xlsx: column screen request {request_size:,} B of {app.config['MAX_CONTENT_LENGTH']:,}")
    assert request_size < app.config["MAX_CONTENT_LENGTH"]
    review = client.post("/upload/columns", data=form, follow_redirects=True).get_data(as_text=True)
    assert "Read 37,000 transactions" in review and "Check the categories" in review


def test_a_remembered_mapping_imports_the_same_layout_without_asking(client, db, logged_in):
    assert "Pick the Date column." in upload(client, UNKNOWN, "s.csv")
    save_column_mapping(db, logged_in, key_of(UNKNOWN), UNKNOWN_ROLES, None)
    page = upload(client, UNKNOWN, "s.csv")
    assert "Read 2 transactions from rows 4–5 (Generic layout)." in page and "Check the categories" in page


def test_a_remembered_mapping_also_settles_ambiguous_dates(client, db, logged_in):
    assert "Dates such as 03/04/2026 could be read two ways." in upload(client, AMBIGUOUS, "s.csv")
    save_column_mapping(db, logged_in, key_of(AMBIGUOUS), AMBIGUOUS_ROLES, "month")
    page = upload(client, AMBIGUOUS, "s.csv")
    assert "Read 2 transactions" in page and "4 Mar 2026" in page and "4 May 2026" in page


def test_one_users_mapping_is_never_used_for_another(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, ravi_id = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")
    save_column_mapping(db, asha_id, key_of(UNKNOWN), UNKNOWN_ROLES, None)
    assert "Read 2 transactions" in upload(asha, UNKNOWN, "s.csv")
    ravi_page = upload(ravi, UNKNOWN, "s.csv")
    assert "Pick the Date column." in ravi_page and "Read 2 transactions" not in ravi_page
    assert get_column_mapping(db, ravi_id, key_of(UNKNOWN)) is None


def test_a_mapping_fits_a_stray_extra_column_but_not_a_missing_one(client, db, logged_in):
    save_column_mapping(db, logged_in, key_of(UNKNOWN), UNKNOWN_ROLES + ["ignore"], None)
    assert "Read 2 transactions" in upload(client, UNKNOWN, "s.csv")
    save_column_mapping(db, logged_in, key_of(UNKNOWN), ["date", "description", "debit", "ignore", "credit"], None)
    assert "Pick the Date column." in upload(client, UNKNOWN, "s.csv")


def test_a_csv_with_our_column_names_but_dr_and_cr_is_read_as_a_bank_statement(client, db, logged_in):
    content = b'Date,Description,Amount,Type\n25/10/2026,UPI-BIGBASKET,"1,250.00",Dr\n26/10/2026,NEFT-SALARY,85000,Cr\n'
    page = upload(client, content, "export.csv")
    assert "Read 2 transactions from rows 2–3 (Generic layout)." in page and "Check the categories" in page


def test_day_first_dates_in_our_own_format_are_read_day_first(client, db, logged_in):
    content = (b"Date,Category,Amount,Type,Description\n05/10/2026,Food,100,Expense,A\n"
               b"12/10/2026,Food,300,Expense,C\n25/10/2026,Food,200,Expense,B\n")
    page = upload(client, content, "export.csv")
    assert "Imported 3 transactions." in page
    assert saved(db, logged_in) == [("2026-10-05", 100.0, "Expense"), ("2026-10-12", 300.0, "Expense"), ("2026-10-25", 200.0, "Expense")]


def test_month_name_dates_in_our_own_format_still_import(client, db, logged_in):
    page = upload(client, b"Date,Category,Amount,Type,Description\n\"Oct 5, 2026\",Food,100,Expense,A\n", "export.csv")
    assert "Imported 1 transaction." in page
    assert saved(db, logged_in) == [("2026-10-05", 100.0, "Expense")]


def test_dr_and_cr_written_with_a_dot_are_read_as_a_bank_statement(client, db, logged_in):
    content = b"Date,Description,Amount,Type\n2026-10-25,UPI-BIGBASKET,1250,Dr.\n2026-10-26,NEFT-SALARY,85000,Cr.\n"
    page = upload(client, content, "export.csv")
    assert "Read 2 transactions from rows 2–3 (Generic layout)." in page and "Check the categories" in page


def test_a_field_longer_than_csvs_limit_keeps_our_format_working_and_explains_a_statement(client, db, logged_in):
    ours = upload(client, b"Date,Category,Amount,Type,Description\n2025-01-01,Food,1,Expense," + b"x" * 200_000 + b"\n", "s.csv")
    assert "Imported 1 transaction." in ours
    statement = upload(client, b"Date,Narration,Debit,Credit\n25/10/2026," + b"x" * 200_000 + b",5,\n", "s.csv")
    assert "could not be read as a CSV" in statement and "Import failed" not in statement


def test_saving_a_mapping_again_replaces_it_and_stores_no_statement_text(db, logged_in):
    save_column_mapping(db, logged_in, key_of(UNKNOWN), ["ignore"] * 4, None)
    save_column_mapping(db, logged_in, key_of(UNKNOWN), UNKNOWN_ROLES, "day")
    assert get_column_mapping(db, logged_in, key_of(UNKNOWN)) == (UNKNOWN_ROLES, "day")
    stored = [tuple(row) for row in db.execute("SELECT * FROM column_mappings")]
    assert len(stored) == 1 and not any(word in repr(stored) for word in ("When", "Money", "Groceries", "500"))


def test_deleting_an_account_removes_only_its_mappings(app, db):
    asha_id, ravi_id = register_and_login(app.test_client(), "asha"), register_and_login(app.test_client(), "ravi")
    for user_id in (asha_id, ravi_id):
        save_column_mapping(db, user_id, key_of(UNKNOWN), UNKNOWN_ROLES, None)
    delete_user(db, asha_id)
    assert get_column_mapping(db, asha_id, key_of(UNKNOWN)) is None
    assert get_column_mapping(db, ravi_id, key_of(UNKNOWN)) == (UNKNOWN_ROLES, None)
