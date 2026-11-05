"""Read bank statements (.csv, .xlsx, .xls) into a grid of text cells.

Everything happens in memory: the upload is never written to disk. Every
problem is raised as a StatementError worded for the person who uploaded it.
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime

import openpyxl
import xlrd

MAX_ROWS = 50_000
MAX_COLUMNS = 100
# A real 2 MB statement unpacks to about 13 MB; anything far past that is a zip bomb.
MAX_XLSX_UNPACKED = 40 * 1024 * 1024

ZIP_MAGIC = b"PK\x03\x04"  # .xlsx is a zip archive
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # old .xls (and password-protected .xlsx)

EMPTY = "The uploaded file is empty."
MACROS = (
    "This workbook contains macros, so FinSight won't open it. In Excel, choose "
    "Save As > Excel Workbook (.xlsx) and upload that copy."
)
TOO_MANY_ROWS = (
    f"This file has more than {MAX_ROWS:,} rows. Split it into smaller files (for example one per "
    "quarter, each with the header row) and upload them one at a time."
)
TOO_WIDE = (
    f"This file has more than {MAX_COLUMNS} columns, which doesn't look like a bank statement. "
    "Check you exported the statement itself, or delete the extra columns and upload again."
)
UNPACKED = (
    f"This workbook unpacks to more than {MAX_XLSX_UNPACKED // (1024 * 1024)} MB, far more than a "
    "statement needs, so it wasn't opened. Save it again from Excel as .xlsx or CSV and upload that."
)
NOT_UTF8 = "The file could not be read as a CSV. Save it as UTF-8 CSV and try again."


class StatementError(ValueError):
    """A problem with the uploaded statement, worded for the person who uploaded it."""


def read_grid(data: bytes, filename: str) -> list[list[str]]:
    """Return the file's first sheet as rows of text cells, without trailing blank cells or rows."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not data:
        raise StatementError(EMPTY)
    if extension == "xlsm":
        raise StatementError(MACROS)
    if extension == "csv":
        return _read_csv(data)
    if extension in ("xlsx", "xls"):
        # Go by the bytes, not the name: an .xlsx saved with an .xls name reads fine either way.
        if data.startswith(ZIP_MAGIC):
            return _read_xlsx(data, filename)
        if data.startswith(OLE_MAGIC):
            return _read_xls(data, filename)
        raise StatementError(
            f"{filename} is named like an Excel file but is {_what_it_is(data)}. Open it in Excel, "
            "save it as .xlsx (Excel Workbook) or CSV, and upload that."
        )
    if extension == "pdf":
        raise StatementError(
            "PDF statements can't be read. Download the statement from net banking as Excel or CSV "
            "instead; most banks offer both next to the PDF option."
        )
    raise StatementError(
        f"FinSight reads .csv, .xlsx and .xls files, and {filename} is not one of them. Download your "
        "statement from net banking as Excel or CSV and upload that."
    )


def _what_it_is(data: bytes) -> str:
    head = data[:512].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if head.startswith(b"%pdf"):
        return "a PDF"
    if head.startswith(b"<"):
        return "a web page (some banks save statements this way)"
    return "plain text"


def _read_csv(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")  # -sig drops the byte-order mark Excel adds to UTF-8 CSVs
    except UnicodeDecodeError as exc:
        raise StatementError(NOT_UTF8) from exc
    try:
        # csv.reader, not pandas: the account details above the header have fewer cells than the rows.
        return _grid(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise StatementError(NOT_UTF8) from exc


def _read_xlsx(data: bytes, filename: str) -> list[list[str]]:
    broken = f"{filename} could not be opened as an Excel workbook. Open it in Excel, save it again as .xlsx or CSV, and upload that."
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise StatementError(broken) from exc
    with archive:
        members = archive.infolist()
    if any(member.filename.lower() == "xl/vbaproject.bin" for member in members):
        raise StatementError(MACROS)  # an .xlsm renamed to .xlsx
    # zipfile never unpacks a member past its declared size, so the sum bounds what openpyxl can load
    # (it reads all of sharedStrings.xml into memory, even in read-only mode).
    if sum(member.file_size for member in members) > MAX_XLSX_UNPACKED:
        raise StatementError(UNPACKED)
    try:
        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # openpyxl raises KeyError, ValueError, its own errors... for a broken file
        raise StatementError(broken) from exc
    try:
        if not book.worksheets:
            raise StatementError(EMPTY)
        # Explicit limits, so a sheet that claims a huge size (its <dimension> tag) can't make us pad
        # millions of empty cells. One extra row and column shows when a file is over the limit.
        rows = book.worksheets[0].iter_rows(max_row=MAX_ROWS + 1, max_col=MAX_COLUMNS + 1, values_only=True)
        return _grid(rows)
    except StatementError:
        raise
    except Exception as exc:  # broken XML inside the sheet surfaces while iterating
        raise StatementError(broken) from exc
    finally:
        book.close()


def _read_xls(data: bytes, filename: str) -> list[list[str]]:
    try:
        # ragged_rows: don't pad every row to the widest one; a single far-off cell would cost millions.
        book = xlrd.open_workbook(file_contents=data, on_demand=True, ragged_rows=True)
        sheet = book.sheet_by_index(0)
    except Exception as exc:  # xlrd raises XLRDError, CompDocError, struct.error... for a broken file
        raise StatementError(
            f"{filename} could not be read as an Excel 97-2003 (.xls) workbook. If it has a password, "
            "remove it; otherwise open it in Excel, save it as .xlsx or CSV, and upload that."
        ) from exc

    def rows():
        for index in range(sheet.nrows):  # at most 65,536 in the .xls format
            types = sheet.row_types(index, 0, MAX_COLUMNS + 1)
            values = sheet.row_values(index, 0, MAX_COLUMNS + 1)
            yield [
                xlrd.xldate_as_datetime(value, book.datemode) if kind == xlrd.XL_CELL_DATE
                else None if kind == xlrd.XL_CELL_ERROR else value
                for kind, value in zip(types, values)
            ]

    try:
        return _grid(rows())
    finally:
        book.release_resources()


def _text(value) -> str:
    """One cell as text. Dates become ISO (2026-11-05), so cells Excel knows are dates are never ambiguous."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _grid(rows) -> list[list[str]]:
    grid = []
    for row in rows:
        cells = [_text(value) for value in row]
        while cells and not cells[-1]:
            cells.pop()
        if len(cells) > MAX_COLUMNS:
            raise StatementError(TOO_WIDE)
        if len(grid) >= MAX_ROWS:  # past the limit only blank rows may follow
            if cells:
                raise StatementError(TOO_MANY_ROWS)
            continue
        grid.append(cells)
    while grid and not grid[-1]:
        grid.pop()
    if not grid:
        raise StatementError(EMPTY)
    return grid
