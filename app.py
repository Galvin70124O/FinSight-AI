import os
import logging
from flask import Flask, flash, redirect, request, url_for

from blueprints.auth import auth_bp
from blueprints.main import main_bp
from config import Config
from database import close_db, init_db
from utils import CSVValidationError, seed_kaggle_transactions


def create_app() -> Flask:
    """Application factory for FinSight AI."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    app.logger.setLevel(logging.INFO)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["TRAINED_MODELS_DIR"], exist_ok=True)

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.teardown_appcontext(close_db)

    @app.errorhandler(CSVValidationError)
    def handle_csv_validation_error(error: CSVValidationError):
        flash(str(error), "danger")
        return redirect(request.referrer or url_for("main.upload"))

    with app.app_context():
        init_db()
        from database import get_db

        db = get_db()
        user_count = db.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        existing_seed = db.execute(
            "SELECT COUNT(*) AS count FROM transactions WHERE source_file = ?",
            ("kaggle_seed",),
        ).fetchone()["count"]
        if existing_seed == 0 and user_count > 0:
            try:
                seed_kaggle_transactions(db, user_id=None)
            except Exception:
                app.logger.exception("Failed to seed Kaggle transactions")
        elif user_count == 0:
            app.logger.info("Skipping Kaggle seed until at least one user exists")

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
