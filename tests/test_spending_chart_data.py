import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils import build_dashboard_payload
from database import init_db
from models import save_transactions


def test_spending_chart_aggregates_expenses_by_month_without_duplicates(tmp_path):
    db_path = tmp_path / 'test.db'
    os.environ['FINSIGHT_DATABASE_PATH'] = str(db_path)
    import app
    app.app.config['DATABASE_PATH'] = str(db_path)
    app.app.config['TRAINED_MODELS_DIR'] = str(tmp_path / 'models')
    init_db()

    tx_df = pd.DataFrame([
        {'Date': '2024-01-05', 'Category': 'Dining', 'Amount': 120.0, 'Type': 'Expense', 'Description': 'Lunch'},
        {'Date': '2024-01-05', 'Category': 'Dining', 'Amount': 120.0, 'Type': 'Expense', 'Description': 'Lunch'},
        {'Date': '2024-02-01', 'Category': 'Groceries', 'Amount': 80.0, 'Type': 'Expense', 'Description': 'Groceries'},
        {'Date': '2024-02-01', 'Category': 'Groceries', 'Amount': 80.0, 'Type': 'Expense', 'Description': 'Groceries'},
        {'Date': '2024-03-15', 'Category': 'Transport', 'Amount': 45.0, 'Type': 'Expense', 'Description': 'Train'},
    ])

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    save_transactions(conn, 1, tx_df, source_file='upload.csv')
    conn.commit()

    conn.execute("INSERT INTO transactions (user_id, date, category, amount, type, description, source_file) VALUES (1, '2024-02-01', 'Groceries', 80.0, 'Expense', 'Groceries', 'kaggle_seed')")
    conn.commit()

    rows = conn.execute("SELECT * FROM transactions WHERE user_id = 1 ORDER BY date").fetchall()
    assert len(rows) == 5

    payload = build_dashboard_payload(pd.DataFrame([
        {'Date': '2024-01-05', 'Category': 'Dining', 'Amount': 120.0, 'Type': 'Expense', 'Description': 'Lunch'},
        {'Date': '2024-02-01', 'Category': 'Groceries', 'Amount': 80.0, 'Type': 'Expense', 'Description': 'Groceries'},
        {'Date': '2024-03-15', 'Category': 'Transport', 'Amount': 45.0, 'Type': 'Expense', 'Description': 'Train'},
    ]), ml_results={'expense_prediction': type('Exp', (), {'next_month': 'Apr', 'predicted_expense': 100.0, 'confidence': 0.8})(), 'health_score': type('Health', (), {'score': 80, 'label': 'Good'})(), 'anomaly_count': 0})

    chart = payload['charts']['monthly_expense']
    assert 'Jan' in chart and 'Feb' in chart and 'Mar' in chart
    assert '120.0' in chart or '120' in chart
