from __future__ import annotations

import re
import sqlite3

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from database import get_db
from models import User, create_user, get_user_by_email, get_user_by_username, verify_password

auth_bp = Blueprint("auth", __name__)


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

        if len(password) < 6:
            flash("Password must be at least 6 characters long.", "danger")
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
        except sqlite3.IntegrityError:
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

        session["user_id"] = user.id
        session["username"] = user.username
        return redirect(url_for("main.dashboard"))

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    """End the current session."""
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
