import pandas as pd

from utils import period_totals


def frame(rows):
    df = pd.DataFrame(rows, columns=["Date", "Amount"])
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def test_same_month_in_different_years_is_not_merged():
    df = frame([("2025-01-10", 500.0), ("2024-01-10", 100.0), ("2024-02-03", 50.0)])

    monthly = period_totals(df, "M")
    totals = dict(zip(monthly["Label"], monthly["Total"]))

    assert totals["Jan 2024"] == 100.0
    assert totals["Jan 2025"] == 500.0


def test_months_are_in_date_order_with_gaps_filled():
    df = frame([("2025-01-10", 500.0), ("2024-11-02", 100.0)])

    monthly = period_totals(df, "M")

    assert monthly["Label"].tolist() == ["Nov 2024", "Dec 2024", "Jan 2025"]
    assert monthly["Total"].tolist() == [100.0, 0.0, 500.0]


def test_quarters_and_years_carry_the_year():
    df = frame([("2024-11-02", 100.0), ("2025-01-10", 500.0), ("2025-02-10", 25.0)])

    assert period_totals(df, "Q")["Label"].tolist() == ["Q4 2024", "Q1 2025"]
    assert period_totals(df, "Q")["Total"].tolist() == [100.0, 525.0]
    assert period_totals(df, "Y")["Label"].tolist() == ["2024", "2025"]
