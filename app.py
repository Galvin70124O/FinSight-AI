import os
import logging
from datetime import date
from flask import Flask, flash, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect

from blueprints.auth import auth_bp
from blueprints.main import main_bp
from config import Config
from database import close_db, init_db
from utils import CSVValidationError, format_day, format_inr, group_digits, plural

csrf = CSRFProtect()


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory for FinSight AI."""
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    app.logger.setLevel(logging.INFO)

    os.makedirs(app.instance_path, exist_ok=True)

    app.jinja_env.filters["inr"] = format_inr
    app.jinja_env.filters["plural"] = plural
    app.jinja_env.filters["day"] = format_day
    app.jinja_env.filters["digits"] = lambda count: group_digits(str(count))
    # The footer's copyright year comes from the clock, never a hard-coded number.
    app.context_processor(lambda: {"current_year": date.today().year})
    csrf.init_app(app)
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.teardown_appcontext(close_db)

    @app.errorhandler(CSVValidationError)
    def handle_csv_validation_error(error: CSVValidationError):
        flash(str(error), "danger")
        return redirect(url_for("main.upload"))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(_error: CSRFError):
        flash("Your session expired or the form was out of date. Please try again.", "danger")
        # Send people back to the form they submitted (logout has no form page).
        return redirect(url_for("main.index") if request.endpoint == "auth.logout" else request.path)

    @app.errorhandler(404)
    def handle_not_found(_error):
        return render_template("404.html"), 404

    @app.errorhandler(413)
    def handle_too_large(_error):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(
            f"That file is too large. The limit is {limit_mb} MB. Split it into smaller CSV files "
            "(for example one per year, each with the header row) and upload them one at a time. "
            "Rows you have already imported are skipped.",
            "danger",
        )
        return redirect(url_for("main.upload"))

    with app.app_context():
        init_db()

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG") == "1"
    # Only listen on every interface in production; locally (and always with
    # the debugger on) stay on 127.0.0.1. HOST overrides both.
    default_host = "0.0.0.0" if Config.IS_PRODUCTION and not debug else "127.0.0.1"
    app.run(
        host=os.environ.get("HOST", default_host),
        port=int(os.environ.get("PORT", 5000)),
        debug=debug,
    )
