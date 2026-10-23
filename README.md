# FinSight AI

FinSight AI is a personal-finance dashboard. You import your bank or expense
transactions as a CSV, and it shows where your money goes: income against
spending month by month, spending by category, a forecast for next month with
an honest range, unusual transactions, and a health score you can see the
working for. Amounts are shown in rupees with Indian digit grouping
(₹1,25,000).

Built with Flask, pandas, scikit-learn and Plotly. Data is stored in SQLite
locally or Postgres in production.

## Screenshots

_Add screenshots here: the dashboard with demo data, and the import page._

## Features

- Accounts with hashed passwords, CSRF-protected forms and POST-only logout.
- CSV import (parsed in memory, never stored on disk; 2 MB limit). Re-importing
  the same rows does not create duplicates.
- **Load demo data** on an empty dashboard: eight months of sample transactions
  loaded into your account only.
- Income, expenses, balance and savings rate.
- Charts: monthly spending, income vs expense per month, spending by category.
  Months are grouped by year and month ("Jan 2025"), so different years never
  merge.
- Next-month spending forecast with an 80% range.
- Unusual-transaction list.
- Financial health score with a breakdown.

## Run it locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt

# Windows PowerShell: $env:FLASK_DEBUG="1"; python app.py
FLASK_DEBUG=1 python app.py
```

Open http://127.0.0.1:5000, create an account, then import a CSV or click
**Load demo data**.

Run the tests with:

```bash
python -m pytest
```

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | random per run (local only) | Signs sessions. **Required in production**; the app refuses to start without it. |
| `ENVIRONMENT` | unset | Set to `production` to enable production checks. Render's `RENDER` variable does the same. |
| `DATABASE_PATH` | `instance/finsight_ai.db` | SQLite file used when `DATABASE_URL` is not set. |
| `DATABASE_URL` | unset | A `postgres://` / `postgresql://` URL switches storage to Postgres. |
| `FLASK_DEBUG` | unset | `1` turns on the debugger and auto-reload (local only). |
| `HOST` / `PORT` | `127.0.0.1` / `5000` | Where `python app.py` listens. It binds to `0.0.0.0` only in production without debug. |

## CSV format

One row per transaction, with a header row. Column names are case-insensitive.

| Column | Required | Example | Notes |
| --- | --- | --- | --- |
| `Date` | yes | `2025-01-31` | Any format pandas can parse; ISO dates are safest. |
| `Category` | yes | `Groceries` | Free text. Anomalies are judged within each category. |
| `Amount` | yes | `2450.50` | Positive number in rupees, no currency symbol or commas. |
| `Type` | yes | `Expense` | `Income` or `Expense`. Other values are skipped. |
| `Description` | yes | `Weekly groceries` | Free text. |

```csv
Date,Category,Amount,Type,Description
2025-01-01,Salary,85000,Income,Monthly salary
2025-01-03,Rent,22000,Expense,Flat rent
2025-01-04,Groceries,2450.50,Expense,Weekly groceries
```

Rows with an unreadable date, a missing or non-positive amount, or an unknown
type are dropped. Exact duplicates (same date, category, amount, type and
description) are imported once. `dataset/sample_transactions.csv` is a full
example.

## How the "AI" works

There is no deep learning here. The app uses small statistical models that you
can check by hand. They are computed once per user when data is imported and
cached in the database (`user_insights` table). No model files are written,
and nothing is shared between users.

**Spending forecast.** Expenses are totalled per calendar month; months with
no spending count as ₹0. A straight-line trend (scikit-learn
`LinearRegression`) is fitted to those totals and extended one month ahead.
The range is a standard 80% regression prediction interval: it comes from how
far past months sat from the trend line, so it is wide when spending is
erratic or history is short.

- Fewer than 3 months: no forecast, just "need at least 3 months of data".
- 3–5 months: forecast shown and labelled *low confidence*.
- 6 or more months: shown normally.

A straight line cannot know about festivals, one-off purchases or a pay rise,
and a single large purchase pulls the trend up. Treat the forecast as a rough
guide.

**Unusual transactions.** Each expense is compared with the median of its own
category, scaled by the median absolute deviation (MAD). This is the
"modified z-score"; an expense is flagged when it scores above 3.5, i.e. it is
much higher than usual. Lower-than-usual amounts are never flagged. If every
amount in a category is identical (rent, a fixed subscription), MAD is 0, and
an amount is flagged only if it is more than 50% above the usual one. Categories with fewer than 4 expenses are skipped
because there is no "usual" yet. Income is never flagged.

**Health score (0–100).** Calculated in one place, `ml/health_score.py`, and
shown with its breakdown on the dashboard:

| Part | Points | Full marks when |
| --- | --- | --- |
| Savings rate | 50 | you keep 20% or more of your income |
| Months covered | 25 | income covered spending in every month |
| Steady spending | 25 | monthly spending barely varies (needs 2+ months) |

If a part cannot be measured yet, the score is scaled to the parts that can.
With no income recorded there is no score; the dashboard asks you to add
income transactions instead. 80+ is "Healthy", 60–79 "Stable", below that
"Needs attention". The weights are a judgement call, not a financial standard.

**Tip.** The dashboard points at your largest *variable* expense category
(categories where every payment is identical, like rent, are skipped). It
shows what that category costs per month on average, and what a 10% cut
would save.

## Deploying on Render

1. Create a Web Service from this repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app`
2. Set `SECRET_KEY` to a long random value. Without it the service will not
   start.
3. **Use Postgres for data you want to keep.** Render's free web services have
   an ephemeral disk that is wiped on every deploy and restart, so a SQLite file
   there loses all accounts and transactions. Create a Postgres database (on
   Render or elsewhere) and set `DATABASE_URL` to its connection string. Tables
   are created automatically on start.

## Project structure

```
app.py               Application factory, CSRF, error handlers
config.py            Settings from environment variables
database.py          SQLite / Postgres connection and schema
models.py            Users, transactions, cached insights
utils.py             CSV cleaning, dashboard numbers, charts, ₹ formatting
blueprints/          Routes (auth, dashboard, import, demo data)
ml/                  Forecast, anomaly detection, health score
templates/, static/  Pages, styles, scripts
dataset/             Demo data (sample_transactions.csv)
tests/               pytest suite
```
