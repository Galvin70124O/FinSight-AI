"""Savings goals: add, change and delete them, with Undo for a delete. Every query is scoped to the signed-in user."""
from __future__ import annotations

import secrets
from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from database import get_db
from ml.goals import GOALS_MAX, check_goal, plan_goal, savings_rate
from models import claim_import_token, create_goal, delete_goal, get_goal, get_goals, update_goal

goals_bp = Blueprint("goals", __name__, url_prefix="/goals")


@goals_bp.before_request
def _signed_in():
    from blueprints.main import require_login  # imported here: main imports goals_section from this module
    return require_login()

UNDO_MAX_AGE = 600  # seconds a deleted goal can be put back
TOO_MANY = f"You have {GOALS_MAX} goals, the most FinSight keeps. Delete one to add another."


def goals_section(db, user_id: int, transactions, today: date) -> dict:
    """What the dashboard's goals section shows: each goal with its plan, and the savings pace behind them."""
    rate = savings_rate(transactions)
    goals = [{**goal, "plan": plan_goal(goal["target_amount"], date.fromisoformat(goal["target_date"]), rate, today)}
             for goal in get_goals(db, user_id)]
    return {"goals": goals, "rate": rate, "full": len(goals) >= GOALS_MAX}


def _undo_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt="goal-undo")


def _back(anchor: str = "goals"):
    return redirect(url_for("main.dashboard") + f"#{anchor}")


def _form(goal: dict | None, values: dict, errors: dict, status: int = 200):
    return render_template("goal_form.html", goal=goal, values=values, errors=errors), status


def _plain(amount: float) -> str:
    """60000.0 -> '60000', 150000.5 -> '150000.50': the amount as the form shows it for editing."""
    return f"{amount:.0f}" if amount == int(amount) else f"{amount:.2f}"


@goals_bp.route("/new", methods=["GET", "POST"])
def new_goal():
    db, user_id = get_db(), session["user_id"]
    if request.method == "GET":
        return _form(None, {}, {})
    values, errors = check_goal(request.form, date.today())
    if not errors and len(get_goals(db, user_id)) >= GOALS_MAX:
        errors["form"] = TOO_MANY
    if errors:
        return _form(None, values, errors, 400)
    create_goal(db, user_id, values["name"], values["target_amount"], values["date"].isoformat())
    flash(f"Added {values['name']}.", "at-goals success")
    return _back()


@goals_bp.route("/<int:goal_id>/edit", methods=["GET", "POST"])
def edit_goal(goal_id: int):
    db, user_id = get_db(), session["user_id"]
    goal = get_goal(db, user_id, goal_id)
    if goal is None:  # someone else's goal looks exactly like a missing one
        abort(404)
    if request.method == "GET":
        return _form(goal, {"name": goal["name"], "target": _plain(goal["target_amount"]), "target_date": goal["target_date"]}, {})
    values, errors = check_goal(request.form, date.today())
    if errors:
        return _form(goal, values, errors, 400)
    if not update_goal(db, user_id, goal_id, values["name"], values["target_amount"], values["date"].isoformat()):
        abort(404)  # deleted in another tab meanwhile
    flash(f"Saved {values['name']}.", "at-goals success")
    return _back()


@goals_bp.route("/<int:goal_id>/delete", methods=["POST"])
def remove_goal(goal_id: int):
    deleted = delete_goal(get_db(), session["user_id"], goal_id)
    if deleted is None:
        abort(404)
    # The dashboard shows this once as the Undo toast. The nonce makes the Undo work once, however often it's sent.
    token = _undo_serializer().dumps({"u": session["user_id"], "n": secrets.token_urlsafe(12), **deleted})
    session["undo_toast"] = {"message": f"Deleted {deleted['name']}.", "token": token, "url": url_for("goals.undo_delete")}
    return _back()


@goals_bp.route("/undo", methods=["POST"])
def undo_delete():
    db, user_id = get_db(), session["user_id"]
    try:
        data = _undo_serializer().loads(request.form.get("undo", ""), max_age=UNDO_MAX_AGE)
    except SignatureExpired:
        flash("Too late to undo: a deleted goal can be put back for 10 minutes. Add it again instead.", "at-goals danger")
        return _back()
    except BadSignature:
        flash("That Undo couldn't be read, so nothing changed.", "at-goals danger")
        return _back()
    if data.get("u") != user_id:
        flash("That Undo belongs to another account, so nothing changed.", "at-goals danger")
        return _back()
    if len(get_goals(db, user_id)) >= GOALS_MAX:
        flash(TOO_MANY, "at-goals danger")
        return _back()
    if not claim_import_token(db, user_id, f"goal-undo:{data['n']}"):  # pressed twice, or the form sent again
        flash(f"{data['name']} is already back.", "at-goals info")
        return _back()
    create_goal(db, user_id, data["name"], data["target_amount"], data["target_date"])  # commits the claim too
    flash(f"Undone: {data['name']} is back.", "at-goals success")
    return _back()
