from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
from flask import current_app


REQUIRED_COLUMNS = ["Date", "Category", "Amount", "Type", "Description"]
logger = logging.getLogger(__name__)
TOP_CATEGORIES = 7
ANOMALY_LIMIT = 10  # the newest ones; the dashboard says how many more there are
OTHER_CATEGORIES = "Everything else"  # not "Other": users often have a category of that name


def format_inr(value: Any, decimals: int | None = None) -> str:
    """Format a number as rupees with Indian digit grouping, e.g. ₹1,25,000.

    With ``decimals=None`` paise are shown only when the amount has them.
    """
    amount = float(value or 0)
    if decimals is None:
        decimals = 0 if round(amount, 2) == round(amount) else 2
    amount = round(amount, decimals)
    sign = "−" if amount < 0 else ""  # U+2212 minus, the width of "+", not a hyphen
    whole, _, fraction = f"{abs(amount):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while head:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(groups)
    return f"{sign}₹{whole}" + (f".{fraction}" if fraction else "")


def inr_axis(max_value: float, ticks: int = 5) -> dict[str, list]:
    """Tick positions and ₹ labels for a Plotly axis (Plotly cannot group digits the Indian way)."""
    if max_value <= 0:
        return {}
    raw_step = max_value / ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw_step)
    values = [step * i for i in range(math.ceil(max_value / step) + 1)]
    return {"tickvals": values, "ticktext": [format_inr(v, 0) for v in values]}


def plural(count: int, word: str) -> str:
    """'1 month', '8 months': English plurals for the counts the app prints."""
    return f"{count} {word}" + ("" if count == 1 else "s")


class CSVValidationError(ValueError):
    """Raised when an uploaded CSV fails validation."""


def empty_dashboard_payload() -> dict[str, Any]:
    """Return the default dashboard payload."""
    return {
        "metrics": {
            "total_income": 0.0,
            "total_expense": 0.0,
            "current_balance": 0.0,
            "savings_rate": 0.0,
            "health_score": None,
        },
        "recent_transactions": [],
        "month_on_month": None,
        "insight": "Upload a transaction file to begin generating insights.",
        "charts": {},
        "summary": {
            "average_spending": 0.0,
            "highest_spending_category": "N/A",
            "monthly_spending_trend": [],
            "category_distribution": [],
            "income_vs_expense": {"labels": [], "income": [], "expense": []},
            "chart_series": {
                "monthly": {"labels": [], "values": [], "counts": []},
                "quarterly": {"labels": [], "values": [], "counts": []},
                "yearly": {"labels": [], "values": [], "counts": []},
            },
        },
        "insights": {"forecast": None, "anomalies": [], "anomaly_count": 0, "health": None, "tip": None},
    }


def spending_tip(expense_df: pd.DataFrame, months: int) -> dict[str, Any] | None:
    """A concrete tip about the largest expense category whose amounts vary.

    Categories where every payment is identical (rent, a fixed subscription)
    are skipped because they cannot be trimmed week to week.
    """
    variable = [category for category, group in expense_df.groupby("Category") if group["Amount"].nunique() > 1]
    if not variable or months < 1:
        return None
    totals = expense_df[expense_df["Category"].isin(variable)].groupby("Category")["Amount"].sum().sort_values(ascending=False)
    monthly_average = float(totals.iloc[0]) / months
    return {
        "category": str(totals.index[0]),
        "monthly_average": round(monthly_average, 2),
        "share": round(float(totals.iloc[0]) / float(expense_df["Amount"].sum()) * 100, 1),
        "saving": round(monthly_average * 0.10, 2),
    }


def allowed_file(filename: str) -> bool:
    """Return whether the file extension is acceptable."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in current_app.config["ALLOWED_EXTENSIONS"]


def clean_transaction_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Validate, clean, and standardize uploaded transaction data."""
    if df is None:
        raise CSVValidationError("The uploaded file is empty.")

    if not isinstance(df, pd.DataFrame):
        raise CSVValidationError("Uploaded file could not be read as a table.")

    if df.empty:
        raise CSVValidationError("The uploaded CSV is empty.")

    df = df.copy()
    df.columns = [str(col).strip() for col in df.columns]

    missing_columns = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing_columns:
        raise CSVValidationError(f"Missing required columns: {', '.join(missing_columns)}")

    df = df.loc[:, REQUIRED_COLUMNS].copy()
    df = df.drop_duplicates()

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Category"] = df["Category"].fillna("Uncategorized").astype(str).str.strip()
    df["Description"] = df["Description"].fillna("No description").astype(str).str.strip()
    df["Type"] = df["Type"].fillna("Expense").astype(str).str.strip().str.title()
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")

    df = df.dropna(subset=["Date", "Category", "Amount", "Type"])
    df = df[df["Amount"] > 0].copy()
    df = df[df["Type"].isin(["Income", "Expense"])].copy()

    if df.empty:
        raise CSVValidationError("No valid transactions remained after cleaning. Please review the uploaded CSV.")

    df = enrich_transaction_dataframe(df)
    return df.reset_index(drop=True)


def read_uploaded_csv(stream) -> pd.DataFrame:
    """Parse an uploaded CSV stream, turning parser errors into user-facing messages."""
    try:
        return pd.read_csv(stream)
    except pd.errors.EmptyDataError as exc:
        raise CSVValidationError("The uploaded file is empty.") from exc
    except (pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise CSVValidationError("The file could not be read as a CSV. Save it as UTF-8 CSV and try again.") from exc


def validate_uploaded_csv(df: pd.DataFrame) -> None:
    """Validate the CSV shape before deeper processing starts."""
    if not isinstance(df, pd.DataFrame):
        raise CSVValidationError("The uploaded file is not a valid CSV table.")
    if df.empty:
        raise CSVValidationError("The uploaded CSV does not contain any rows.")

    required_columns = [column.lower() for column in REQUIRED_COLUMNS]
    normalized_columns = {str(column).strip().lower() for column in df.columns}
    missing = [column for column in required_columns if column not in normalized_columns]
    if missing:
        raise CSVValidationError("Missing required columns: " + ", ".join(column.title() for column in missing))


def normalize_transaction_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize transaction column names."""
    rename_map = {
        "date": "Date",
        "category": "Category",
        "amount": "Amount",
        "type": "Type",
        "description": "Description",
    }
    normalized = df.copy()
    normalized.columns = [str(col).strip() for col in normalized.columns]
    return normalized.rename(columns={column: rename_map[column.lower()] for column in normalized.columns if column.lower() in rename_map})


def prepare_transactions(transactions_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize and deduplicate income and expense transactions for analytics and charting."""
    if transactions_df is None or transactions_df.empty:
        return pd.DataFrame(columns=["Date", "Category", "Amount", "Type", "Description", "Year", "Month", "Month_Number", "Day", "Day_Name", "Week", "Quarter", "Weekend"])

    df = normalize_transaction_columns(transactions_df)

    if "Date" not in df.columns:
        return pd.DataFrame(columns=["Date", "Category", "Amount", "Type", "Description", "Year", "Month", "Month_Number", "Day", "Day_Name", "Week", "Quarter", "Weekend"])

    if "Type" not in df.columns:
        df["Type"] = df.get("Transaction_Type", "Expense")
    if "Category" not in df.columns:
        df["Category"] = "Uncategorized"
    if "Description" not in df.columns:
        df["Description"] = "No description"

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Category"] = df["Category"].fillna("Uncategorized").astype(str).str.strip()
    df["Description"] = df["Description"].fillna("No description").astype(str).str.strip()
    df["Type"] = df["Type"].fillna("Expense").astype(str).str.strip().str.title()
    df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")

    df = df.dropna(subset=["Date", "Category", "Amount", "Type"]).copy()
    df = df[df["Amount"] > 0].copy()
    df = df[df["Type"].isin(["Income", "Expense"])].copy()
    df = df.drop_duplicates(subset=["Date", "Category", "Amount", "Type", "Description"], keep="first")

    if df.empty:
        return pd.DataFrame(columns=["Date", "Category", "Amount", "Type", "Description", "Year", "Month", "Month_Number", "Day", "Day_Name", "Week", "Quarter", "Weekend"])

    df = enrich_transaction_dataframe(df)
    return df.reset_index(drop=True)


def enrich_transaction_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Create calendar-based columns from the transaction date."""
    df = df.copy()
    if "Date" not in df.columns:
        raise ValueError("Transactions must include a Date column.")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"]).copy()

    if df.empty:
        raise ValueError("No valid transaction dates were found.")

    df["Year"] = df["Date"].dt.year
    df["Month"] = df["Date"].dt.strftime("%b")
    df["Month_Number"] = df["Date"].dt.month
    df["Day"] = df["Date"].dt.day
    df["Day_Name"] = df["Date"].dt.day_name()
    df["Week"] = df["Date"].dt.isocalendar().week.astype(int)
    df["Quarter"] = df["Date"].dt.quarter
    df["Weekend"] = df["Date"].dt.dayofweek >= 5
    return df.reset_index(drop=True)


PERIOD_LABELS = {
    "M": lambda period: period.strftime("%b %Y"),
    "Q": lambda period: f"Q{period.quarter} {period.year}",
    "Y": lambda period: str(period.year),
}


def period_totals(df: pd.DataFrame, freq: str, start=None, end=None) -> pd.DataFrame:
    """Sum amounts per calendar period in date order, filling empty periods with 0.

    Grouping by the full period (e.g. Jan 2025) keeps the same month in
    different years apart. ``start``/``end`` widen the range so several
    series can share one axis.
    """
    periods = df["Date"].dt.to_period(freq)
    start = start if start is not None else (periods.min() if not df.empty else None)
    end = end if end is not None else (periods.max() if not df.empty else None)
    if start is None or end is None:
        return pd.DataFrame(columns=["Period", "Label", "Total", "Count"])
    grouped = df.groupby(periods)["Amount"].agg(Total="sum", Count="size")
    full_range = pd.period_range(start, end, freq=freq)
    grouped = grouped.reindex(full_range, fill_value=0)
    return pd.DataFrame({
        "Period": full_range,
        "Label": [PERIOD_LABELS[freq](period) for period in full_range],
        "Total": grouped["Total"].astype(float).round(2).to_numpy(),
        "Count": grouped["Count"].astype(int).to_numpy(),
    })


def series_dict(summary: pd.DataFrame) -> dict[str, list]:
    return {
        "labels": summary["Label"].tolist(),
        "values": summary["Total"].tolist(),
        "counts": summary["Count"].tolist(),
    }


def spending_view(summary: pd.DataFrame, axis_title: str, axis_floor: float = 0) -> dict[str, Any]:
    """One Month/Quarter/Year view of the spending chart, ready for Plotly.update.

    ``axis_floor`` stretches the ₹ axis to fit something drawn beyond the totals (the forecast band).
    """
    axis = inr_axis(max(summary["Total"].max() if not summary.empty else 0, axis_floor))
    return {
        "x": summary["Label"].tolist(),
        "y": summary["Total"].tolist(),
        "customdata": [[format_inr(total), int(count)] for total, count in zip(summary["Total"], summary["Count"])],
        "tickvals": axis.get("tickvals", []),
        "ticktext": axis.get("ticktext", []),
        "axis_title": axis_title,
    }


def spending_figure(month_view: dict[str, Any], forecast: dict[str, Any]) -> dict[str, Any]:
    """Monthly spending as a line, continued by next month's forecast with its 80% range as a band.

    Traces carry a ``meta`` role (actual / band-low / band / forecast) so the browser can colour
    them from the theme. The band runs from the last real month, where it is zero-width, to the
    forecast month, where it spans low to high.
    """
    data = [{
        "type": "scatter", "mode": "lines+markers", "meta": "actual", "name": "Spending",
        "x": month_view["x"], "y": month_view["y"], "customdata": month_view["customdata"],
        "hovertemplate": "<b>%{x}</b><br>Spent %{customdata[0]}<br>%{customdata[1]} transactions<extra></extra>",
    }]
    if forecast.get("enough_data") and month_view["x"]:
        x = [month_view["x"][-1], forecast["month"]]
        last = month_view["y"][-1]
        label = f"Forecast {format_inr(forecast['predicted'], 0)}<br>likely {format_inr(forecast['low'], 0)} to {format_inr(forecast['high'], 0)}"
        data += [
            {"type": "scatter", "mode": "lines", "meta": "band-low", "x": x, "y": [last, forecast["low"]], "hoverinfo": "skip", "showlegend": False},
            {"type": "scatter", "mode": "lines", "meta": "band", "fill": "tonexty", "x": x, "y": [last, forecast["high"]], "hoverinfo": "skip", "showlegend": False},
            {"type": "scatter", "mode": "lines+markers", "meta": "forecast", "name": "Forecast", "x": x, "y": [last, forecast["predicted"]],
             "customdata": ["", label], "hovertemplate": "%{customdata}<extra></extra>"},
        ]
    return {"data": data, "layout": {"yaxis": {"tickvals": month_view["tickvals"], "ticktext": month_view["ticktext"]}}}


def month_on_month(labels: list[str], income: list[float], expense: list[float]) -> dict[str, Any] | None:
    """Latest month against the one before: income, spending and amount saved, with the change in rupees.

    The three lists are the dashboard's per-month series (same months, oldest first).
    Returns None with fewer than two months, when there is nothing to compare.
    """
    if len(labels) < 2:
        return None

    def figure(now: float, before: float) -> dict[str, float]:
        return {"value": round(now, 2), "previous": round(before, 2), "change": round(now - before, 2)}

    saved = [i - e for i, e in zip(income, expense)]
    return {
        "month": labels[-1],
        "previous_month": labels[-2],
        "income": figure(income[-1], income[-2]),
        "expense": figure(expense[-1], expense[-2]),
        "saved": figure(saved[-1], saved[-2]),
    }


def build_dashboard_payload(transactions_df: pd.DataFrame, insights: dict[str, Any] | None = None, recent_limit: int = 10) -> dict[str, Any]:
    """Create layout-ready analytics for the dashboard.

    ``insights`` is the cached output of ml.pipeline.compute_insights for this user.
    """
    if transactions_df is None or transactions_df.empty:
        return empty_dashboard_payload()

    transactions_df = prepare_transactions(transactions_df)
    if transactions_df.empty:
        return empty_dashboard_payload()

    income_df = transactions_df[transactions_df["Type"] == "Income"]
    expense_df = transactions_df[transactions_df["Type"] == "Expense"]

    total_income = float(income_df["Amount"].sum())
    total_expense = float(expense_df["Amount"].sum())
    current_balance = total_income - total_expense
    savings_rate = round((current_balance / total_income * 100), 1) if total_income else 0.0
    insights = insights or {}
    health = insights.get("health")

    # Every series spans the same months, from the first to the last transaction.
    first_month = transactions_df["Date"].min().to_period("M")
    last_month = transactions_df["Date"].max().to_period("M")
    monthly_summary = period_totals(expense_df, "M", first_month, last_month)
    monthly_income = period_totals(income_df, "M", first_month, last_month)
    quarterly_summary = period_totals(expense_df, "Q", first_month.asfreq("Q"), last_month.asfreq("Q"))
    yearly_summary = period_totals(expense_df, "Y", first_month.asfreq("Y"), last_month.asfreq("Y"))

    category_totals = expense_df.groupby("Category")["Amount"].sum().sort_values(ascending=False)
    highest_spending_category = category_totals.index[0] if not category_totals.empty else "N/A"
    average_spending = round(float(expense_df["Amount"].mean()), 2) if not expense_df.empty else 0.0

    recent_transactions_df = transactions_df.sort_values("Date", ascending=False).head(recent_limit).copy()
    recent_transactions_df["Date"] = recent_transactions_df["Date"].dt.strftime("%Y-%m-%d")
    recent_transactions = recent_transactions_df.to_dict("records")

    category_frame = category_totals.reset_index()
    category_frame.columns = ["Category", "Total"]
    if len(category_frame) > TOP_CATEGORIES + 1:
        # Fold the tail into one row so the listed shares always add up to 100%.
        rest = pd.DataFrame([{"Category": OTHER_CATEGORIES, "Total": category_frame["Total"].iloc[TOP_CATEGORIES:].sum()}])
        category_frame = pd.concat([category_frame.iloc[:TOP_CATEGORIES], rest], ignore_index=True)

    months = len(monthly_summary)
    if total_income and current_balance < 0:
        # A negative "share kept" (e.g. -3589%) means nothing to a reader; say the gap in rupees.
        insight = (
            f"Over {plural(months, 'month')} you earned {format_inr(total_income, 0)} and spent "
            f"{format_inr(total_expense, 0)}: {format_inr(-current_balance, 0)} more than you earned."
        )
    elif total_income:
        insight = (
            f"Over {plural(months, 'month')} you earned {format_inr(total_income, 0)} and spent "
            f"{format_inr(total_expense, 0)}, keeping {savings_rate}% of your income."
        )
    else:
        insight = f"You have recorded {format_inr(total_expense)} of spending. Add income transactions to see your savings rate."

    # The Month/Quarter/Year buttons swap these views into the spending chart.
    forecast = insights.get("forecast") or {}
    band_top = (forecast.get("high") or 0) if forecast.get("enough_data") else 0
    spending_views = {
        "month": spending_view(monthly_summary, "Month", band_top),
        "quarter": spending_view(quarterly_summary, "Quarter"),
        "year": spending_view(yearly_summary, "Year"),
    }

    charts = {
        "spending": spending_figure(spending_views["month"], forecast),
        "spending_views": spending_views,
        "cashflow": {
            # Grouped bars, not smoothed areas: a spline bends past the real monthly totals.
            "data": [
                {
                    "type": "bar",
                    "name": name,
                    "meta": name.lower(),
                    "x": series["Label"].tolist(),
                    "y": series["Total"].tolist(),
                    "customdata": [format_inr(total, 0) for total in series["Total"]],
                    "hovertemplate": f"{name}: %{{customdata}}<extra></extra>",
                }
                for name, series in (("Income", monthly_income), ("Spending", monthly_summary))
            ],
            "layout": {"barmode": "group", "yaxis": inr_axis(max(monthly_income["Total"].max(), monthly_summary["Total"].max()))},
        },
    }

    return {
        "metrics": {
            "total_income": round(total_income, 2),
            "total_expense": round(total_expense, 2),
            "current_balance": round(current_balance, 2),
            "savings_rate": savings_rate,
            "health_score": health["score"] if health else None,
        },
        "recent_transactions": recent_transactions,
        "month_on_month": month_on_month(monthly_summary["Label"].tolist(), monthly_income["Total"].tolist(), monthly_summary["Total"].tolist()),
        "insight": insight,
        "charts": charts,
        "summary": {
            "average_spending": average_spending,
            "highest_spending_category": highest_spending_category,
            "monthly_spending_trend": monthly_summary.drop(columns="Period").to_dict("records"),
            "category_distribution": category_frame.to_dict("records"),
            "income_vs_expense": {
                "labels": monthly_summary["Label"].tolist(),
                "income": monthly_income["Total"].tolist(),
                "expense": monthly_summary["Total"].tolist(),
            },
            "chart_series": {
                "monthly": series_dict(monthly_summary),
                "quarterly": series_dict(quarterly_summary),
                "yearly": series_dict(yearly_summary),
            },
        },
        "insights": {
            "forecast": insights.get("forecast"),
            "anomalies": insights.get("anomalies", [])[:ANOMALY_LIMIT],
            "anomaly_count": len(insights.get("anomalies", [])),
            "health": health,
            "tip": spending_tip(expense_df, months),
        },
    }

