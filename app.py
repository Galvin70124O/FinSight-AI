import io
import os
from datetime import date
from flask import Flask, Request, flash, jsonify, redirect, render_template, request, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect

from blueprints.auth import auth_bp
from blueprints.goals import goals_bp
from blueprints.main import main_bp
from blueprints.recurring import recurring_bp
from config import Config
from database import close_db, init_db
from utils import CSVValidationError, format_day, format_inr, group_digits, plural

csrf = CSRFProtect()

# Scripts, styles, fonts and the chart library are all served by the app itself, so 'self' covers them.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # Plotly adds empty <style> blocks and fills them from script. This is the hash of an empty block, so it
    # lets those in and no inline CSS text at all.
    "style-src 'self' 'sha256-47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU='",
    "style-src-attr 'unsafe-inline'",  # the widths of the overview's category bars and health meter
    "img-src 'self' data:",  # the select arrow in style.css is an SVG data URL
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


class InMemoryRequest(Request):
    """Keeps uploaded files in memory. Werkzeug would move any upload over 500 KB into a temporary file on
    disk; MAX_CONTENT_LENGTH (2 MB) still caps how much is held."""

    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        return io.BytesIO()


def create_app(test_config: dict | None = None) -> Flask:
    """Application factory for FinSight AI."""
    app = Flask(__name__, instance_relative_config=True)
    app.request_class = InMemoryRequest
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    app.logger.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())

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
    app.register_blueprint(goals_bp)
    app.register_blueprint(recurring_bp)
    app.teardown_appcontext(close_db)

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Frame-Options"] = "DENY"  # frame-ancestors for browsers that predate it
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.errorhandler(CSVValidationError)
    def handle_csv_validation_error(error: CSVValidationError):
        flash(str(error), "danger")
        return redirect(url_for("main.upload"))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(_error: CSRFError):
        if request.accept_mimetypes.best == "application/json":  # the page script: say so in the row, no redirect
            return jsonify(ok=False, error="Your session expired or the page is out of date. Reload the page and try again."), 400
        flash("Your session expired or the form was out of date. Please try again.", "danger")
        # Send people back to the form they submitted. A POST-only address has no page of its own
        # (redirecting there would show "405 Method Not Allowed"), so those go to the page with the form.
        if request.url_rule is not None and "GET" in request.url_rule.methods:
            return redirect(request.path)
        pages = {"auth.logout": "main.index", "auth.delete_account": "main.upload", "main.save_review": "main.upload"}
        return redirect(url_for(pages.get(request.endpoint, "main.dashboard")))

    @app.errorhandler(404)
    def handle_not_found(_error):
        return render_template("404.html"), 404

    @app.errorhandler(500)
    def handle_server_error(_error):
        # Flask has already logged the exception; the page says nothing about it.
        return render_template("500.html"), 500

    @app.errorhandler(413)
    def handle_too_large(_error):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(
            f"That file is too large. The limit is {limit_mb} MB. Split it into smaller files "
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
