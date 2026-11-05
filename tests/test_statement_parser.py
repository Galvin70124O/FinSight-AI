"""Reading bank statements into a grid: formats, limits and the files that are refused."""
import io
import zipfile
from datetime import date, datetime

import pytest

from conftest import xls_bytes, xlsx_bytes
from ml import statement_parser
from ml.statement_parser import StatementError, read_grid

HEADER_BLOCK = [["Asha Rao"], ["Account No", "XXXXXX4521"], []]
HEADER = ["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"]


def test_a_csv_with_a_byte_order_mark_and_ragged_rows_is_read_cell_by_cell():
    text = "﻿Asha Rao\nAccount No,XXXXXX4521\n\nDate,Narration,Withdrawal Amt.,Deposit Amt.\n05/11/26,UPI-किराना स्टोर-தமிழ்,\"1,25,000.00\",\n"
    grid = read_grid(text.encode("utf-8"), "statement.csv")
    assert grid[0] == ["Asha Rao"]  # no ﻿ stuck to the first cell
    assert grid[2] == []
    assert grid[4] == ["05/11/26", "UPI-किराना स्टोर-தமிழ்", "1,25,000.00"]


def test_an_xlsx_gives_text_cells_with_dates_as_iso_and_whole_numbers_without_point_zero():
    rows = HEADER_BLOCK + [HEADER, [datetime(2026, 11, 5), "UPI-ZOMATO", 1250.0, None, 40210.5]]
    grid = read_grid(xlsx_bytes(rows), "statement.xlsx")
    assert grid[3] == HEADER
    assert grid[4] == ["2026-11-05", "UPI-ZOMATO", "1250", "", "40210.5"]


def test_an_old_xls_reads_its_date_cells_as_dates():
    rows = [HEADER, [date(2026, 11, 5), "NEFT-HOSTEL FEE", 18000, None, 22000.75]]
    grid = read_grid(xls_bytes(rows), "statement.xls")
    assert grid == [HEADER, ["2026-11-05", "NEFT-HOSTEL FEE", "18000", "", "22000.75"]]


def test_an_xlsx_saved_with_an_xls_name_is_still_read():
    assert read_grid(xlsx_bytes([HEADER]), "statement.xls") == [HEADER]


@pytest.mark.parametrize("name", ["statement.xlsm", "STATEMENT.XLSM"])
def test_a_macro_workbook_is_refused_by_its_name(name):
    with pytest.raises(StatementError, match="contains macros"):
        read_grid(xlsx_bytes([HEADER]), name)


def test_a_macro_workbook_renamed_to_xlsx_is_refused():
    buffer = io.BytesIO(xlsx_bytes([HEADER]))
    with zipfile.ZipFile(buffer, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"macro code")
    with pytest.raises(StatementError, match="contains macros"):
        read_grid(buffer.getvalue(), "statement.xlsx")


def test_a_zip_bomb_is_refused_before_openpyxl_opens_it():
    buffer = io.BytesIO(xlsx_bytes([HEADER]))
    with zipfile.ZipFile(buffer, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/media/padding.bin", b"\0" * (statement_parser.MAX_XLSX_UNPACKED + 1))
    assert len(buffer.getvalue()) < 2 * 1024 * 1024  # small enough to pass the upload limit
    with pytest.raises(StatementError, match="unpacks to more than 40 MB"):
        read_grid(buffer.getvalue(), "statement.xlsx")


@pytest.mark.parametrize("data, what", [
    (b"Date,Category,Amount,Type,Description\n2025-01-01,Rent,22000,Expense,Flat rent\n", "plain text"),
    (b"<html><table><tr><td>Date</td></tr></table></html>", "a web page"),
    (b"%PDF-1.7 statement", "a PDF"),
])
def test_text_named_like_an_excel_file_says_what_it_really_is(data, what):
    with pytest.raises(StatementError, match=f"is named like an Excel file but is {what}"):
        read_grid(data, "statement.xls")


def test_a_broken_workbook_gets_a_specific_message():
    with pytest.raises(StatementError, match="could not be opened as an Excel workbook"):
        read_grid(b"PK\x03\x04 not really a zip", "statement.xlsx")
    with pytest.raises(StatementError, match=r"could not be read as an Excel 97-2003 \(.xls\) workbook"):
        read_grid(statement_parser.OLE_MAGIC + b"\0" * 600, "statement.xls")


@pytest.mark.parametrize("name, message", [
    ("statement.pdf", "PDF statements can't be read"),
    ("statement.numbers", "FinSight reads .csv, .xlsx and .xls files, and statement.numbers is not one of them"),
    ("statement", "FinSight reads .csv, .xlsx and .xls files"),
])
def test_other_file_types_are_refused_with_a_specific_message(name, message):
    with pytest.raises(StatementError, match=message):
        read_grid(b"anything", name)


def test_a_csv_that_is_not_utf8_gets_todays_message():
    with pytest.raises(StatementError, match="Save it as UTF-8 CSV"):
        read_grid("Date,Narration\n05/11/26,Caf\xe9\n".encode("cp1252"), "statement.csv")


@pytest.mark.parametrize("data", [b"", b"\n\n,,,\n"])
def test_an_empty_file_is_refused(data):
    with pytest.raises(StatementError, match="empty"):
        read_grid(data, "statement.csv")


@pytest.mark.parametrize("build, name", [
    (lambda rows: "\n".join(",".join(row) for row in rows).encode(), "statement.csv"),
    (xlsx_bytes, "statement.xlsx"),
    (xls_bytes, "statement.xls"),
])
def test_row_and_column_limits_hold_for_every_format(monkeypatch, build, name):
    monkeypatch.setattr(statement_parser, "MAX_ROWS", 5)
    monkeypatch.setattr(statement_parser, "MAX_COLUMNS", 4)
    assert len(read_grid(build([["a", "b"]] * 5 + [[""]] * 3), name)) == 5  # trailing blank rows are fine
    with pytest.raises(StatementError, match="more than 50,000 rows"):
        read_grid(build([["a", "b"]] * 6), name)
    with pytest.raises(StatementError, match="more than 100 columns"):
        read_grid(build([["a"] * 5]), name)
