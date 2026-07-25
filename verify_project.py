import pandas as pd
from app import app
from database import get_db
from utils import clean_transaction_dataframe

with app.app_context():
    db = get_db()
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('users','transactions')").fetchall()
    print('tables_ok', tables)
    df = pd.read_csv('dataset/sample_transactions.csv')
    cleaned = clean_transaction_dataframe(df)
    print(cleaned[['Date', 'Year', 'Month', 'Month_Number', 'Day', 'Day_Name', 'Week', 'Quarter', 'Weekend']].head().to_string(index=False))
