from __future__ import annotations

import re

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from database import IntegrityError, get_db
from models import User, create_user, delete_user, get_user_by_email, get_user_by_username, verify_password

auth_bp = Blueprint("auth", __name__)

# Length beats symbol rules (NIST SP 800-63B). Checked when a password is set, never at
# login, so accounts created under the old 6-character rule keep working.
MIN_PASSWORD_LENGTH = 12


@auth_bp.app_context_processor
def password_rule():
    return {"min_password_length": MIN_PASSWORD_LENGTH}


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Register a new user account."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username or not email or not password:
            flash("Please complete all fields.", "danger")
            return render_template("register.html")

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("Please enter a valid email address.", "danger")
            return render_template("register.html")

        if len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Use a password of at least {MIN_PASSWORD_LENGTH} characters. A few unrelated words work well.", "danger")
            return render_template("register.html")

        if password != confirm_password:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        db = get_db()
        if get_user_by_username(db, username) is not None or get_user_by_email(db, email) is not None:
            flash("A user with that username or email already exists.", "danger")
            return render_template("register.html")

        try:
            create_user(db, username, email, password)
        except IntegrityError:
            db.rollback()
            flash("That username or email is already registered.", "danger")
            return render_template("register.html")

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate an existing user."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        db = get_db()
        user = get_user_by_username(db, username)
        if user is None or not verify_password(user, password):
            flash("Incorrect username or password.", "danger")
            return render_template("login.html")

        # Start a fresh session so nothing from before login carries over.
        session.clear()
        session["user_id"] = user.id
        session["username"] = user.username
        return redirect(url_for("main.dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """End the current session."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/account/delete", methods=["POST"])
def delete_account():
    """Delete the signed-in user's account and all their data, after re-checking the password."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    back = url_for("main.upload") + "#delete"

    user = get_user_by_username(get_db(), session.get("username", ""))
    if user is None or user.id != session["user_id"] or not verify_password(user, request.form.get("password", "")):
        flash("That password is not right, so nothing was deleted.", "delete")
        return redirect(back)
    if request.form.get("confirm", "").strip() != "DELETE":
        flash("Type DELETE in capitals to confirm. Nothing was deleted.", "delete")
        return redirect(back)

    delete_user(get_db(), user.id)
    session.clear()
    flash("Your account, transactions and insights have been deleted.", "success")
    return redirect(url_for("main.index"))
