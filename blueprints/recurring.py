"""Recurring payments: dismiss one that isn't really recurring, or restore it. Every query is scoped to the signed-in user."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, request, session, url_for

from database import get_db
from ml.recurring import PERIODS, find_recurring, is_dismissed, paise
from models import dismiss_recurring, get_dismissed, restore_recurring

recurring_bp = Blueprint("recurring", __name__, url_prefix="/recurring")

PAYEE_MAX = 200


@recurring_bp.before_request
def _signed_in():
    from blueprints.main import require_login  # imported here: main imports this module's section helper
    return require_login()


def recurring_section(db, user_id: int, transactions) -> dict:
    """What the dashboard's recurring section shows: each series with the (payee, period, paise) its button posts.
    A dismissed one carries the stored values, not its current amount, so Restore matches exactly after a price change."""
    dismissed = get_dismissed(db, user_id)
    shown, hidden = [], []
    for series in find_recurring(transactions):
        stored = next((d for d in dismissed if is_dismissed(series, {d})), None)
        if stored is None:
            shown.append({"series": series, "key": (series.payee, series.period_days, paise(series.amount))})
        else:
            hidden.append({"series": series, "key": stored})
    return {"shown": shown, "dismissed": hidden}


def _series() -> tuple[str, int, int]:
    """(payee, period in days, amount in paise) from the form; 400 for anything a dashboard button never sends."""
    payee = request.form.get("payee", "").strip()
    try:
        period, amount = int(request.form.get("period_days", "")), int(request.form.get("amount_paise", ""))
    except ValueError:
        abort(400)
    if not payee or len(payee) > PAYEE_MAX or period not in PERIODS or amount <= 0:
        abort(400)
    return payee, period, amount


@recurring_bp.route("/dismiss", methods=["POST"])
def dismiss():
    payee, period, amount = _series()
    dismiss_recurring(get_db(), session["user_id"], payee, period, amount)
    flash(f"Dismissed {payee}. It's under the dismissed payments below if you want it back.", "at-recurring success")
    return redirect(url_for("main.dashboard") + "#recurring")


@recurring_bp.route("/restore", methods=["POST"])
def restore():
    payee, period, amount = _series()  # the stored values from the dismissed list, so the match is exact
    restore_recurring(get_db(), session["user_id"], payee, period, amount)
    flash(f"Restored {payee}.", "at-recurring success")
    return redirect(url_for("main.dashboard") + "#recurring")
