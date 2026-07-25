import os
from flask import Flask

from blueprints.auth import auth_bp
from blueprints.main import main_bp
from config import Config
from database import close_db, init_db
from utils import seed_kaggle_transactions


def create_app() -> Flask:
    """Application factory for FinSight AI."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["TRAINED_MODELS_DIR"], exist_ok=True)

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.teardown_appcontext(close_db)

    with app.app_context():
        init_db()
        from database import get_db

        db = get_db()
        existing_seed = db.execute("SELECT COUNT(*) AS count FROM transactions WHERE source_file = ?", ("kaggle_seed",)).fetchone()["count"]
        if existing_seed == 0:
            seed_kaggle_transactions(db, user_id=None)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
