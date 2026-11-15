"""Turn a bank statement (.csv, .xlsx, .xls) into FinSight's Date/Category/Amount/Type/Description rows.

1. read_grid: the first sheet as rows of text cells, in memory only (the upload is never written to disk).
2. locate: the header row (the best match for known column names in the first 40 rows) and the rows below it.
3. guess_roles / convert: which column is which, then dates, amounts and Debit/Credit into Expense/Income.
Column names and date formats per bank are data, in bank_presets.json. Every problem is a StatementError,
or an entry in Result.errors, worded for the person who uploaded the file.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from functools import cached_property
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pandas as pd
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


# --- Finding the header and mapping the columns ------------------------------------------------

ROLES = ("date", "description", "debit", "credit", "amount", "type", "category", "ignore")  # the mapping screen's choices
HEADER_SCAN_ROWS = 40
NO_TRANSACTIONS = ("The file has column names in row {row} but no transactions below them. Check the dates the "
                   "statement covers and download it again.")
FEWEST_NEGATIVE_DEBITS = 3  # with fewer, all-negative debits could be refunds as easily as a bank's formatting
SHOWN_ERRORS = 5

_PRESETS = {
    key: preset
    for key, preset in json.loads(Path(__file__).with_name("bank_presets.json").read_text(encoding="utf-8")).items()
    if not key.startswith("_")
}


def _name(cell: str) -> str:
    """How header cells and preset names are compared: 'Withdrawal Amount (INR )' -> 'withdrawal amount'."""
    words = re.sub(r"[^a-z0-9]+", " ", re.sub(r"\([^)]*\)", " ", cell.lower())).split()
    return " ".join(word for word in words if word not in ("inr", "rs"))


_ROLE_OF: dict[str, str] = {}  # normalised column name -> role; Generic comes first in the file, so it wins a clash
for _preset in _PRESETS.values():
    for _role, _names in _preset["columns"].items():
        for _column in _names:
            _ROLE_OF.setdefault(_name(_column), _role)
_BANK_NAMES = {
    key: {_name(column) for names in preset["columns"].values() for column in names}
    for key, preset in _PRESETS.items() if key != "generic"
}

_DATE_LIKE = re.compile(r"^\d{1,4}[-/. ]+(\d{1,2}|[a-z]{3,9})[-/., ]+\d{2,4}\b", re.IGNORECASE)
_SEPARATOR = re.compile(r"^[\s*=_\-.~]*$")  # blank cells and rows of *** or ---
_SUMMARY = re.compile(
    r"\b(total|totals|opening balance|closing balance|statement summary|brought forward|carried forward|"
    r"dr count|cr count|debit count|credit count|legends?|end of statement|generated on|computer generated)\b"
)


def _looks_like_date(text: str) -> bool:
    return bool(_DATE_LIKE.match(text.strip()))


def _letter(index: int) -> str:
    """Spreadsheet column letter: 0 -> A, 26 -> AA."""
    letters, index = "", index + 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


@dataclass
class Layout:
    """Where the transactions are: the header row (its index in the grid) and every row below it.
    Never changed after it is made, so the values below are worked out once."""

    header_index: int
    header: list[str]
    rows: list[list[str]]

    @cached_property
    def width(self) -> int:
        return max(len(row) for row in [self.header, *self.rows])

    @cached_property
    def labels(self) -> list[str]:
        """What to call each column in messages: its header text, or its letter when the header cell is blank.
        A name used twice gets its letter too ("Ref no (column H)"), so the two can be told apart."""
        header = self.header + [""] * (self.width - len(self.header))
        names = [" ".join(cell.lower().split()) for cell in header]
        return [(f"{cell} (column {_letter(i)})" if names.count(names[i]) > 1 else cell) if cell else f"column {_letter(i)}"
                for i, cell in enumerate(header)]

    @cached_property
    def key(self) -> str:
        """Fingerprint of the header row, to remember a confirmed mapping. Position-free: a longer block of
        account details above the header next month still matches."""
        return hashlib.sha256("\x1f".join(" ".join(cell.lower().split()) for cell in self.header).encode()).hexdigest()

    @cached_property
    def preset(self) -> str:
        """The bank whose column names cover most of the header (60% at least, with one name only that bank
        uses), else 'generic'. A tie is 'generic' too: a bank's date formats must not be applied on a guess."""
        cells = {_name(cell) for cell in self.header} - {""}
        scores = []
        for key, names in _BANK_NAMES.items():
            others = set().union(*(other for name, other in _BANK_NAMES.items() if name != key))
            if cells & names - others:
                scores.append((len(cells & names) / len(cells), key))
        scores.sort(reverse=True)
        if scores and scores[0][0] >= 0.6 and (len(scores) == 1 or scores[0][0] > scores[1][0]):
            return scores[0][1]
        return "generic"

    @cached_property
    def preset_label(self) -> str:
        return _PRESETS[self.preset]["label"]


def locate(grid: list[list[str]]) -> Layout:
    """Find the header row: in the first 40 rows, the row with the most known column names that has a date,
    a description and a money column. Failing that (an unknown layout), the first row of three or more text
    cells followed by a row with a date, so the mapping screen can be shown."""
    best, best_score = None, 0
    for index, row in enumerate(grid[:HEADER_SCAN_ROWS]):
        roles = {_ROLE_OF.get(_name(cell)) for cell in row}
        if not ({"date", "value_date"} & roles and "description" in roles and {"debit", "credit", "amount"} & roles):
            continue
        score = sum(_name(cell) in _ROLE_OF for cell in row)
        if score > best_score:
            best, best_score = index, score
    if best is None:
        best = next((index for index in range(min(len(grid), HEADER_SCAN_ROWS)) if _could_be_header(grid, index)), None)
    filled = [index for index, row in enumerate(grid) if any(row)]
    if best is None and len(filled) == 1 and _are_names(grid[filled[0]]):  # a header row of names FinSight doesn't know
        raise StatementError(NO_TRANSACTIONS.format(row=filled[0] + 1))
    if best is None:
        raise StatementError(
            "Couldn't find the header row (the row of column names such as Date, Narration, Debit and Credit) in "
            f"the first {HEADER_SCAN_ROWS} rows. Check the file is a list of transactions, or add a header row above them."
        )
    return Layout(best, grid[best], grid[best + 1:])


def _are_names(row: list[str], least: int = 2) -> bool:
    """At least `least` filled cells, none of them a date or an amount: a row that could be column names."""
    cells = [cell for cell in row if cell]
    return len(cells) >= least and not any(_looks_like_date(cell) or _amount(cell) is not None for cell in cells)


def _could_be_header(grid, index) -> bool:
    if not _are_names(grid[index], 3):
        return False
    following = next((row for row in grid[index + 1:index + 4] if any(row)), [])
    return sum(bool(cell) for cell in following) >= 3 and any(_looks_like_date(cell) for cell in following)


def guess_roles(layout: Layout) -> list[str]:
    """One role per column from the known column names. The first column wins a role; Value Date is used
    only when there is no transaction date."""
    roles = [_ROLE_OF.get(_name(cell), "ignore") for cell in layout.header]
    roles += ["ignore"] * (layout.width - len(roles))
    if "date" not in roles and "value_date" in roles:
        roles[roles.index("value_date")] = "date"
    seen = set()
    for i, role in enumerate(roles):
        if role == "value_date" or role in seen:
            roles[i] = "ignore"
        seen.add(roles[i])
    return roles


def fit_roles(roles: list[str], width: int) -> list[str] | None:
    """A saved mapping fitted to this file's width: next month's file may have a stray cell further right.
    None when a chosen column is beyond this file's last column."""
    if any(role != "ignore" for role in roles[width:]):
        return None
    return roles[:width] + ["ignore"] * (width - len(roles))


def check_roles(roles: list[str], labels: list[str]) -> list[str]:
    """What is wrong with a mapping, worded for the mapping screen. Empty when it can be used."""
    if len(roles) != len(labels) or any(role not in ROLES for role in roles):
        return ["Choose what each column holds from the list above it."]
    picked = {role: [labels[i] for i, r in enumerate(roles) if r == role] for role in ROLES}
    problems = [f"Pick the {role.title()} column." for role in ("date", "description") if not picked[role]]
    problems += [f"Pick one {role.title()} column: you picked {_and(picked[role])}." for role in ROLES[:-1] if len(picked[role]) > 1]
    money = {role for role in ("debit", "credit", "amount") if picked[role]}
    if money == {"debit"}:
        problems.append("Pick the Credit column too, or one Amount column instead of Debit.")
    elif money == {"credit"}:
        problems.append("Pick the Debit column too, or one Amount column instead of Credit.")
    elif not money:
        problems.append("Pick the columns with the money: Debit and Credit, or one Amount column.")
    elif "amount" in money and len(money) > 1:
        problems.append("Pick either Debit and Credit, or one Amount column, not both.")
    if picked["type"] and "amount" not in money:
        problems.append("A Type (Dr/Cr) column only works with an Amount column.")
    return problems


# --- Amounts and dates ------------------------------------------------------------------------------

_NUMBER = re.compile(r"^(?P<open>[-+(])?(?P<number>\d+(?:\.\d+)?)\)?(?P<minus>-)?(?P<side>dr|cr)?\.?$")
_DEBIT_WORDS = {"dr", "d", "debit", "withdrawal", "expense"}
_CREDIT_WORDS = {"cr", "c", "credit", "deposit", "income"}


def says_dr_or_cr(text: str) -> bool:
    """A bank's word for which way money went ('Dr.', 'CR', 'Debit'), read as convert() reads a Type cell.
    FinSight's own Income and Expense don't count."""
    return _name(text) in (_DEBIT_WORDS | _CREDIT_WORDS) - {"income", "expense"}


def _amount(text: str) -> tuple[float, str | None] | None:
    """'1,25,000.00 Cr' -> (125000.0, 'cr'); '-500' and '(500)' -> (-500.0, None); blank or '-' -> (0.0, None).
    None when the text is not an amount. Commas go whatever their grouping, so Indian 1,25,000 reads too."""
    text = text.lower()
    for noise in ("₹", "rs.", "inr", ",", " ", " "):
        text = text.replace(noise, "")
    if text in ("", "-", "--", "nil"):
        return 0.0, None
    match = _NUMBER.match(text)
    if not match:
        return None
    value = float(match["number"])
    if match["open"] in ("-", "(") or match["minus"]:
        value = -value
    return value, match["side"]


_TIME = re.compile(r"\s+\d{1,2}:\d{2}(:\d{2})?(\s*[ap]\.?m\.?)?$", re.IGNORECASE)
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # what Excel date cells become in read_grid; FinSight's own CSV dates
DAY_FIRST = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y", "%d.%m.%y")
MONTH_FIRST = ("%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y")
UNAMBIGUOUS = ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %y", "%d/%b/%Y", "%d %B %Y", "%d-%B-%Y",
               "%b %d, %Y", "%B %d, %Y")  # month first with its name ("Oct 5, 2026"): still unambiguous


def _parse_date(text: str, fmt: str) -> date | None:
    try:
        return datetime.strptime(_TIME.sub("", text.strip()), fmt).date()  # a time after the date is dropped
    except ValueError:
        return None


def _date_format(texts: set[str], preferred, order: str | None) -> tuple[str | None, tuple[str, str] | None]:
    """(the format that reads every date, None), or (None, (day-first, month-first)) when both read every
    date differently and `order` doesn't say which. The bank's own formats are tried first, then formats that
    can't be misread (month names, year first), then day-first and month-first."""
    def reads_all(fmt):
        return all(_parse_date(text, fmt) for text in texts)

    for fmt in (*preferred, *UNAMBIGUOUS):
        if reads_all(fmt):
            return fmt, None
    day = next((fmt for fmt in DAY_FIRST if reads_all(fmt)), None)
    month = next((fmt for fmt in MONTH_FIRST if reads_all(fmt)), None)
    if day and month and order != "day" and any(_parse_date(t, day) != _parse_date(t, month) for t in texts):
        return (month, None) if order == "month" else (None, (day, month))
    if day or month:
        return day or month, None
    # No format reads every date: use the one that reads most, so the rows it can't read are named.
    every = (*preferred, *UNAMBIGUOUS, *DAY_FIRST, *MONTH_FIRST)
    return max(every, key=lambda fmt: sum(bool(_parse_date(text, fmt)) for text in texts)), None


@dataclass
class Result:
    """What convert() made of a statement. `frame` is set only when there is nothing to fix."""

    frame: pd.DataFrame | None = None
    errors: list[str] = field(default_factory=list)  # the first few problems, worded for the mapping screen
    error_count: int = 0
    date_example: tuple[str, str, str] | None = None  # (text, read day-first, read month-first) when ambiguous
    skipped: int = 0  # rows with neither a debit nor a credit
    first_row: int = 0  # spreadsheet row numbers of the first and last transaction
    last_row: int = 0

    @property
    def ok(self) -> bool:
        return self.frame is not None

    def problem(self, message: str) -> None:
        self.error_count += 1
        if len(self.errors) < SHOWN_ERRORS:
            self.errors.append(message)


def convert(layout: Layout, roles: list[str], date_order: str | None = None) -> Result:
    """Read the transactions below the header with this mapping. Row numbers in messages are the rows you
    see in Excel, counting the account details above the header.

    Blank rows, rows of *** and the header repeated after a page break are passed over. Rows with neither a
    debit nor a credit (an opening balance, the second line of a wrapped narration, page footers) are skipped
    and counted. The transactions end at a summary row (Total, Closing balance...) without a date, or at three
    blank rows in a row. A row with money but no readable date stops the import: dropping it would make the
    balance wrong without anyone noticing."""
    result = Result()
    labels = layout.labels
    for message in check_roles(roles, labels):
        result.problem(message)
    if result.error_count:
        return result
    column = {role: roles.index(role) for role in set(roles) - {"ignore"}}
    debits_negative = _debits_negative(layout, column, labels, result)
    if result.error_count:
        return result
    header_names = [_name(cell) for cell in layout.header]
    found = []  # (row number, date text, signed amount, description, category)
    blanks, said_which_way, end = 0, False, len(layout.rows)
    for offset, row in enumerate(layout.rows):
        number = layout.header_index + 2 + offset
        cells = row + [""] * (layout.width - len(row))
        if all(_SEPARATOR.match(cell) for cell in cells):
            blanks += 1
            if found and blanks >= 3:
                end = offset
                break
            continue
        blanks = 0
        if [_name(cell) for cell in cells[:len(header_names)]] == header_names:
            continue
        date_text = cells[column["date"]].strip()
        # Not the description: a wrapped narration such as "TOTAL ENERGIES" must not end the statement.
        others = " ".join(_name(cell) for i, cell in enumerate(cells) if i != column["description"])
        if not _looks_like_date(date_text) and _SUMMARY.search(others):
            if found:
                end = offset
                break
            result.skipped += 1
            continue
        money = _signed_amount(cells, column, labels, number, result, debits_negative)
        if money is None:
            continue
        if not money[0]:
            result.skipped += 1
            continue
        said_which_way = said_which_way or money[1]
        found.append((number, date_text, money[0], cells[column["description"]].strip() or "No description",
                      cells[column["category"]].strip() if "category" in column else ""))
    for offset in range(end + 1, len(layout.rows)):  # a transaction after the end must never vanish quietly
        cells = layout.rows[offset] + [""] * (layout.width - len(layout.rows[offset]))
        if _looks_like_date(cells[column["date"]]) and any(
                (_amount(cells[column[role]]) or (0.0,))[0] for role in ("debit", "credit", "amount") if role in column):
            result.problem(
                f"Row {layout.header_index + 2 + offset} looks like a transaction, but the statement seemed to end at row "
                f"{layout.header_index + 2 + end} (a summary row or blank rows). Remove the rows in between and upload again."
            )
            break
    if "amount" in column and "type" not in column and found and not said_which_way:
        result.problem(
            f"Every amount in {labels[column['amount']]} is positive and nothing says which are money in and which "
            "are money out. Pick the column that says Dr or Cr as Type, or upload a file with Debit and Credit columns."
        )
    if not found and not result.error_count:
        result.problem(
            f"No transactions were found below the header row (row {layout.header_index + 1}). Check which columns "
            "hold the date and the money."
        )
    if result.error_count:
        return result

    texts = {text for _, text, *_ in found if text and not ISO_DATE.match(text)}
    fmt, both = _date_format(texts, _PRESETS[layout.preset]["date_formats"], date_order)
    if both:
        # An example whose two readings differ, such as 03/04/26: 3 Apr 2026 or 4 Mar 2026.
        text = next(t for t in sorted(texts) if _parse_date(t, both[0]) != _parse_date(t, both[1]))
        result.date_example = (text, *(f"{d.day} {d:%b %Y}" for d in (_parse_date(text, f) for f in both)))
        return result

    rows = []
    for number, text, amount, description, category in found:
        day = _parse_date(text, "%Y-%m-%d" if ISO_DATE.match(text) else fmt) if text else None
        if day is None:
            result.problem(f"Row {number}: '{text}' is not a valid date." if text else f"Row {number}: the date is blank.")
            continue
        rows.append((day.isoformat(), category, abs(amount), "Expense" if amount < 0 else "Income", description))
    if result.error_count:
        return result
    result.first_row, result.last_row = found[0][0], found[-1][0]
    result.frame = pd.DataFrame(rows, columns=["Date", "Category", "Amount", "Type", "Description"])
    return result


def _debits_negative(layout: Layout, column: dict[str, int], labels: list[str], result: Result) -> bool:
    """Whether the Debit column writes every debit as negative, (500.00) or -500: then the sign is only formatting
    and they are money out. A few negatives among positive debits stay refunds. With fewer than
    FEWEST_NEGATIVE_DEBITS debits, all negative, it can't be told, so the user is asked to check the column."""
    if "debit" not in column:
        return False
    at = column["debit"]
    debits = [(layout.header_index + 2 + offset, row[at].strip(), value) for offset, row in enumerate(layout.rows)
              if at < len(row) and (value := (_amount(row[at]) or (0.0,))[0])]
    if not debits or any(value > 0 for *_, value in debits):
        return False
    if len(debits) < FEWEST_NEGATIVE_DEBITS:
        number, text, _ = debits[0]
        result.problem(
            f"Row {number}: '{text}' in {labels[at]} is negative, and so is every other debit. With so few debits FinSight "
            "can't tell whether they are money out or refunds, so check that column."
        )
    return True


def _signed_amount(cells, column, labels, number, result, debits_negative=False) -> tuple[float, bool] | None:
    """(amount, whether the file said which way it goes): negative is money out, 0.0 is no money.
    None after recording a problem with the row."""
    def read(role):
        value = _amount(cells[column[role]])
        if value is None:
            result.problem(f"Row {number}: '{cells[column[role]].strip()}' in {labels[column[role]]} is not an amount.")
        return value

    if "amount" not in column:
        debit, credit = read("debit"), read("credit")
        if debit is None or credit is None:
            return None
        if debit[0] and credit[0]:
            result.problem(
                f"Row {number} has both a debit ({cells[column['debit']].strip()}) and a credit ({cells[column['credit']].strip()})."
            )
            return None
        for role, (value, side), wrong, way in (("debit", debit, "cr", "out"), ("credit", credit, "dr", "in")):
            if value and side == wrong:
                result.problem(f"Row {number}: '{cells[column[role]].strip()}' says {wrong.title()}, but "
                               f"{labels[column[role]]} is the column for money {way}.")
                return None
        # A negative debit is money in, as some banks write refunds, unless every debit is written that way.
        return credit[0] - (abs(debit[0]) if debits_negative else debit[0]), True
    amount = read("amount")
    if amount is None:
        return None
    value, side = amount
    if not value:
        return 0.0, False
    if "type" in column:
        word = _name(cells[column["type"]])
        if word in _DEBIT_WORDS | _CREDIT_WORDS:
            if side and side != ("dr" if word in _DEBIT_WORDS else "cr"):
                result.problem(f"Row {number}: '{cells[column['amount']].strip()}' in {labels[column['amount']]} says "
                               f"{side.title()}, but {labels[column['type']]} says {cells[column['type']].strip()}.")
                return None
            return (-abs(value) if word in _DEBIT_WORDS else abs(value)), True
        result.problem(f"Row {number}: '{cells[column['type']].strip()}' in {labels[column['type']]} is neither Dr nor Cr.")
        return None
    if side:
        return (-abs(value) if side == "dr" else abs(value)), True
    return value, value < 0
