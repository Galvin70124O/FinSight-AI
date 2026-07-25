# FinSight AI

FinSight AI is an AI-powered personal finance analytics platform built with Flask, SQLite, Pandas, NumPy, scikit-learn, and Plotly. The application supports secure authentication, CSV upload, transaction cleaning, financial analytics, and modular machine-learning-ready pipelines for expense prediction, financial health scoring, and anomaly detection.

## Features

- Secure user registration and login
- CSV transaction import with validation and cleaning
- Interactive dashboards with Plotly charts
- Monthly and category-based financial analysis
- Modular analytics and ML pipeline scaffolding

## Project Structure

- app.py: Flask application factory
- blueprints/: route organization via Flask blueprints
- database.py: SQLite database helpers
- models.py: authentication and transaction models
- utils.py: data cleaning, analytics, and chart generation
- ml/: machine-learning modules
- templates/: HTML views
- static/: CSS, JS, images
- dataset/: example data
- uploads/: uploaded CSV files
- trained_models/: model artifacts

## Installation

1. Create and activate a virtual environment.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run the application:
   ```bash
   python app.py
   ```
4. Open http://127.0.0.1:5000/

## Notes

The ML modules are designed to be modular and retrainable. They currently provide a full pipeline structure for future model training and persistence.
