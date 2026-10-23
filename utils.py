from __future__ import annotations

import logging
import math
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from flask import current_app


REQUIRED_COLUMNS = ["Date", "Category", "Amount", "Type", "Description"]
logger = logging.getLogger(__name__)


def format_inr(value: Any, decimals: int | None = None) -> str:
    """Format a number as rupees with Indian digit grouping, e.g. ₹1,25,000.

    With ``decimals=None`` paise are shown only when the amount has them.
    """
    amount = float(value or 0)
    if decimals is None:
        decimals = 0 if round(amount, 2) == round(amount) else 2
    amount = round(amount, decimals)
    sign = "-" if amount < 0 else ""
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
        "insights": {"forecast": None, "anomalies": [], "health": None, "tip": None},
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

    months = len(monthly_summary)
    if total_income:
        insight = (
            f"Over {months} month(s) you earned {format_inr(total_income)} and spent "
            f"{format_inr(total_expense)}, keeping {savings_rate}% of your income."
        )
    else:
        insight = f"You have recorded {format_inr(total_expense)} of spending. Add income transactions to see your savings rate."

    charts = {
        "monthly_expense": plot_div(
            go.Figure(
                data=[go.Scatter(
                    x=monthly_summary["Label"],
                    y=monthly_summary["Total"],
                    mode="lines",
                    line=dict(color="#2dd4bf", width=3, shape="spline"),
                    fill="tozeroy",
                    fillcolor="rgba(45, 212, 191, 0.16)",
                    hovertemplate="<b>%{x}</b><br>Total expense: %{customdata[0]}<br>Transactions: %{customdata[1]}<extra></extra>",
                    customdata=[[format_inr(total), count] for total, count in zip(monthly_summary["Total"], monthly_summary["Count"])],
                )]
            ).update_layout(
                template="plotly_white",
                margin=dict(l=25, r=25, t=35, b=30),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                hovermode="x",
                xaxis=dict(title="Month"),
                yaxis=dict(title="Total expense", **inr_axis(monthly_summary["Total"].max())),
            )
        ),
        "income_vs_expense": plot_div(
            go.Figure(
                data=[
                    go.Bar(
                        name=name,
                        x=series["Label"],
                        y=series["Total"],
                        marker_color=color,
                        customdata=[format_inr(total) for total in series["Total"]],
                        hovertemplate=f"{name}: %{{customdata}}<extra></extra>",
                    )
                    for name, series, color in (("Income", monthly_income, "#2563eb"), ("Expense", monthly_summary, "#ef4444"))
                ]
            ).update_layout(
                barmode="group",
                template="plotly_white",
                margin=dict(l=25, r=25, t=35, b=30),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                hovermode="x unified",
                legend=dict(orientation="h", y=1.12),
                yaxis=inr_axis(max(monthly_income["Total"].max(), monthly_summary["Total"].max())),
            )
        ),
        "category_pie": plot_div(
            go.Figure(
                data=[go.Pie(
                    labels=category_frame["Category"],
                    values=category_frame["Total"],
                    customdata=[format_inr(total) for total in category_frame["Total"]],
                    hovertemplate="<b>%{label}</b><br>%{customdata}<br>%{percent}<extra></extra>",
                    marker=dict(colors=px.colors.sequential.Emrld),
                )]
            ).update_layout(
                title="Spending Distribution",
                template="plotly_white",
                margin=dict(l=25, r=25, t=60, b=30),
                paper_bgcolor="rgba(0,0,0,0)",
            )
        ),
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
            "anomalies": insights.get("anomalies", []),
            "health": health,
            "tip": spending_tip(expense_df, months),
        },
    }


def plot_div(fig: Any) -> str:
    """Render a Plotly figure as HTML for embedding in templates."""
    return fig.to_html(full_html=False, include_plotlyjs="cdn")
