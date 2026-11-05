"""Bank statements: reading the file, finding the transactions and reading dates and amounts."""
import csv
import io
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from conftest import xls_bytes, xlsx_bytes
from ml import statement_parser
from ml.statement_parser import StatementError, _amount, check_roles, convert, guess_roles, locate, read_grid

FIXTURES = Path(__file__).parent / "fixtures" / "statements"

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


# --- Finding the transactions and reading them ----------------------------------------------------

def fixture_rows(bank):
    with open(FIXTURES / f"{bank}.csv", encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def parse(grid, roles=None, date_order=None):
    layout = locate(grid)
    return layout, convert(layout, roles or guess_roles(layout), date_order)


def csv_grid(text):
    return read_grid(text.encode("utf-8"), "statement.csv")


@pytest.mark.parametrize("bank, preset, header_row, count, income, expense, first_row, last_row, skipped", [
    ("hdfc", "hdfc", 8, 6, 125850.0, 25955.0, 10, 17, 1),  # skipped: the **Continue** page-break row
    ("sbi", "sbi", 12, 4, 95000.0, 35410.75, 13, 16, 0),
    ("icici", "icici", 4, 4, 88000.0, 5711.0, 5, 9, 1),  # skipped: a narration wrapped onto a second row
    ("axis", "axis", 7, 4, 110412.0, 6240.5, 8, 11, 0),
    ("kotak", "kotak", 7, 3, 9000.0, 2457.6, 8, 10, 0),
])
def test_each_banks_statement_is_found_and_read(bank, preset, header_row, count, income, expense, first_row, last_row, skipped):
    data = (FIXTURES / f"{bank}.csv").read_bytes()
    layout, result = parse(read_grid(data, f"{bank}.csv"))
    assert (layout.preset, layout.header_index + 1) == (preset, header_row)
    assert result.ok, result.errors
    frame = result.frame
    assert len(frame) == count
    assert frame.loc[frame.Type == "Income", "Amount"].sum() == pytest.approx(income)
    assert frame.loc[frame.Type == "Expense", "Amount"].sum() == pytest.approx(expense)
    assert (result.first_row, result.last_row, result.skipped) == (first_row, last_row, skipped)
    assert list(frame.columns) == ["Date", "Category", "Amount", "Type", "Description"]
    assert frame.Date.str.match(r"^2026-10-\d\d$").all()


@pytest.mark.parametrize("bank", ["hdfc", "icici"])
def test_the_same_statement_as_xlsx_reads_the_same(bank):
    from_csv = parse(read_grid((FIXTURES / f"{bank}.csv").read_bytes(), "s.csv"))[1].frame
    from_xlsx = parse(read_grid(xlsx_bytes(fixture_rows(bank)), "s.xlsx"))[1].frame
    assert from_xlsx.equals(from_csv)


def test_an_xlsx_with_real_date_and_number_cells_reads():
    rows = [["DETAILED STATEMENT"], [],
            ["S No.", "Value Date", "Transaction Date", "Cheque Number", "Transaction Remarks",
             "Withdrawal Amount (INR )", "Deposit Amount (INR )", "Balance (INR )"],
            [1, datetime(2026, 10, 3), datetime(2026, 10, 2), None, "UPI/swiggy", 412.5, 0, 38588],
            [2, datetime(2026, 10, 4), datetime(2026, 10, 4), None, "NEFT-SALARY", 0, 88000, 126588]]
    frame = parse(read_grid(xlsx_bytes(rows), "s.xlsx"))[1].frame
    assert frame.values.tolist() == [["2026-10-02", "", 412.5, "Expense", "UPI/swiggy"],
                                     ["2026-10-04", "", 88000.0, "Income", "NEFT-SALARY"]]


def test_an_old_sbi_xls_with_date_cells_reads():
    rows = [["Account Name       :", "Mr. ARJUN MENON"], [],
            ["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"],
            [date(2026, 10, 2), date(2026, 10, 2), "BY TRANSFER-NEFT-SALARY", "", None, 95000.0, 153200.0],
            [date(2026, 10, 6), date(2026, 10, 6), "TO TRANSFER-UPI-BIGBASKET", "", 3410.75, None, 149789.25]]
    layout, result = parse(read_grid(xls_bytes(rows), "statement.xls"))
    assert layout.preset == "sbi"
    assert result.frame.values.tolist() == [["2026-10-02", "", 95000.0, "Income", "BY TRANSFER-NEFT-SALARY"],
                                            ["2026-10-06", "", 3410.75, "Expense", "TO TRANSFER-UPI-BIGBASKET"]]


def test_one_amount_column_with_a_dr_cr_column():
    layout, result = parse(csv_grid(
        "Tran Date,Value Date,Transaction Particulars,Chq No,Amount(INR),DR/CR,Balance(INR),Branch Name\n"
        "01-10-2026,01-10-2026,UPI/BIGBASKET,-,\"1,240.50\",DR,\"62,759.50\",MG ROAD\n"
        "07-10-2026,07-10-2026,NEFT/SALARY,-,\"1,10,000.00\",CR,\"1,72,759.50\",MG ROAD\n"))
    assert layout.preset == "axis"
    assert result.frame[["Amount", "Type"]].values.tolist() == [[1240.5, "Expense"], [110000.0, "Income"]]


def test_dr_and_cr_written_after_the_amount():
    result = parse(csv_grid("Date,Description,Amount,Balance\n05/11/2026,Groceries,\"1,250.00 Dr\",100\n"
                            "16/11/2026,Salary,\"85,000.00 Cr\",200\n"))[1]
    assert result.frame[["Amount", "Type"]].values.tolist() == [[1250.0, "Expense"], [85000.0, "Income"]]


@pytest.mark.parametrize("text, expected", [
    ("1,25,000.00", (125000.0, None)), ("1,250.00 Cr", (1250.0, "cr")), ("500 DR", (500.0, "dr")),
    ("(500)", (-500.0, None)), ("-500", (-500.0, None)), ("500-", (-500.0, None)), ("₹ 1,250", (1250.0, None)),
    ("Rs. 99.50", (99.5, None)), ("", (0.0, None)), ("-", (0.0, None)), ("12a.50", None),
])
def test_amounts_in_the_ways_banks_write_them(text, expected):
    assert _amount(text) == expected


def test_a_negative_debit_is_money_in():
    result = parse(csv_grid("Date,Narration,Withdrawal Amt.,Deposit Amt.\n05/11/26,REFUND,-499.00,\n"))[1]
    assert result.frame[["Amount", "Type"]].values.tolist() == [[499.0, "Income"]]


GENERIC_AMBIGUOUS = "Date,Description,Debit,Credit,Balance\n03/04/2026,Groceries,500,,100\n05/04/2026,Salary,,9000,200\n"


def test_ambiguous_dates_in_an_unknown_layout_ask_day_first_or_month_first():
    layout, result = parse(csv_grid(GENERIC_AMBIGUOUS))
    assert layout.preset == "generic" and not result.ok
    assert result.date_example == ("03/04/2026", "3 Apr 2026", "4 Mar 2026")
    assert parse(csv_grid(GENERIC_AMBIGUOUS), date_order="day")[1].frame.Date.tolist() == ["2026-04-03", "2026-04-05"]
    assert parse(csv_grid(GENERIC_AMBIGUOUS), date_order="month")[1].frame.Date.tolist() == ["2026-03-04", "2026-05-04"]


def test_one_day_past_12_settles_the_order_for_the_whole_file():
    result = parse(csv_grid(GENERIC_AMBIGUOUS + "13/04/2026,Rent,22000,,300\n"))[1]
    assert result.frame.Date.tolist() == ["2026-04-03", "2026-04-05", "2026-04-13"]


def test_a_banks_preset_settles_ambiguous_dates_day_first():
    result = parse(csv_grid("Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance\n"
                            "03/04/26,UPI-ZOMATO,1,03/04/26,640.00,,100\n"))[1]
    assert result.frame.Date.tolist() == ["2026-04-03"]


def test_a_row_with_money_and_a_bad_date_stops_the_import_with_its_row_number():
    result = parse(csv_grid("Account,XXXX1234\n\nDate,Description,Debit,Credit\n"
                            "28/02/2026,Rent,22000,\n31/02/2026,Groceries,500,\n,Cash,100,\n"))[1]
    assert not result.ok
    assert result.errors == ["Row 5: '31/02/2026' is not a valid date.", "Row 6: the date is blank."]


def test_only_the_first_five_problems_are_listed_but_all_are_counted():
    bad = "".join(f"bad{n},Item,1,\n" for n in range(8))
    result = parse(csv_grid("Date,Description,Debit,Credit\n01/02/2026,First,5,\n" + bad))[1]
    assert (len(result.errors), result.error_count) == (5, 8)


def test_a_row_with_both_a_debit_and_a_credit_is_a_problem():
    result = parse(csv_grid("Date,Description,Debit,Credit\n05/11/2026,Odd row,100,200\n"))[1]
    assert result.errors == ["Row 2 has both a debit (100) and a credit (200)."]


def test_an_amount_column_that_never_says_which_way_is_a_problem():
    result = parse(csv_grid("Date,Description,Amount\n05/11/2026,Groceries,500\n06/11/2026,Rent,22000\n"))[1]
    assert result.errors[0].startswith("Every amount in Amount is positive and nothing says which are money in")


def test_an_unknown_layout_is_found_and_needs_its_columns_picked():
    grid = csv_grid("My bank export\n\nWhen,What,Money out,Money in\n25/11/2026,Groceries,500,\n26/11/2026,Salary,,9000\n")
    layout = locate(grid)
    assert layout.header == ["When", "What", "Money out", "Money in"]
    assert guess_roles(layout) == ["ignore"] * 4
    assert convert(layout, guess_roles(layout)).errors == [
        "Pick the Date column.", "Pick the Description column.",
        "Pick the columns with the money: Debit and Credit, or one Amount column."]
    result = convert(layout, ["date", "description", "debit", "credit"])
    assert result.frame[["Amount", "Type"]].values.tolist() == [[500.0, "Expense"], [9000.0, "Income"]]


@pytest.mark.parametrize("roles, message", [
    (["date", "date", "description", "debit", "credit"], "Pick one Date column: you picked Txn Date and Value Date."),
    (["date", "ignore", "description", "debit", "ignore"], "Pick the Credit column too, or one Amount column instead of Debit."),
    (["date", "ignore", "description", "amount", "credit"], "Pick either Debit and Credit, or one Amount column, not both."),
    (["date", "ignore", "description", "debit", "type"], "A Type (Dr/Cr) column only works with an Amount column."),
    (["date", "ignore", "description", "debit", "balance"], "Choose what each column holds from the list above it."),
    (["date", "ignore"], "Choose what each column holds from the list above it."),
])
def test_mapping_problems_name_the_columns(roles, message):
    assert message in check_roles(roles, ["Txn Date", "Value Date", "Narration", "Debit", "Credit"])


def test_value_date_is_used_only_without_a_transaction_date():
    both = locate(csv_grid("S No.,Value Date,Transaction Date,Transaction Remarks,Withdrawal Amount (INR ),Deposit Amount (INR )\n"))
    assert guess_roles(both) == ["ignore", "ignore", "date", "description", "debit", "credit"]
    only_value = locate(csv_grid("Value Date,Narration,Debit,Credit\n"))
    assert guess_roles(only_value) == ["date", "description", "debit", "credit"]


def test_the_header_is_found_in_row_35_but_not_after_row_40():
    lines = [f"Account detail line {n}" for n in range(34)]
    layout, result = parse(csv_grid("\n".join(lines + ["Date,Narration,Debit,Credit", "25/11/2026,Tea,20,"])))
    assert (layout.header_index + 1, result.first_row) == (35, 36)
    late = csv_grid("\n".join([f"Line {n}" for n in range(40)] + ["Date,Narration,Debit,Credit", "25/11/2026,Tea,20,"]))
    with pytest.raises(StatementError, match="find the header row"):
        locate(late)


def test_a_40_column_statement_reads():
    header = ["Date", "Narration", "Debit", "Credit"] + [f"Extra {n}" for n in range(36)]
    row = ["25/11/2026", "Groceries", "500", ""] + [f"x{n}" for n in range(36)]
    layout, result = parse(read_grid(xlsx_bytes([header, row]), "wide.xlsx"))
    assert layout.width == 40 and result.frame.Amount.tolist() == [500.0]


def test_hindi_and_tamil_narrations_are_kept():
    result = parse(csv_grid("Date,Narration,Debit,Credit\n25/11/2026,UPI-किराना स्टोर-தமிழ் கடை,250,\n"))[1]
    assert result.frame.Description.tolist() == ["UPI-किराना स्टोर-தமிழ் கடை"]


def test_a_one_row_statement_reads_and_a_header_alone_says_so():
    assert len(parse(csv_grid("Date,Narration,Debit,Credit\n25/11/2026,Tea,20,\n"))[1].frame) == 1
    assert parse(csv_grid("Date,Narration,Debit,Credit\n"))[1].errors == [
        "No transactions were found below the header row (row 1). Check which columns hold the date and the money."]


def test_the_layout_key_ignores_position_case_and_spacing_but_not_the_names():
    first = locate(csv_grid("Name,Asha\nDate,Narration,Debit,Credit\n"))
    later = locate(csv_grid("Name,Asha\nBranch,Pune\n\nDATE, Narration ,Debit,Credit\n"))
    other = locate(csv_grid("Date,Narration,Withdrawal,Deposit\n"))
    assert first.key == later.key != other.key
