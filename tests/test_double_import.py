"""A double tap on Import sends two requests at once: the rows must be saved once, on SQLite and Postgres."""
import threading
import uuid

import pandas as pd
import pytest

import models
from database import get_db
from models import claim_import_token, create_user, save_transactions

ROWS = pd.DataFrame({
    "Date": pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-03"]),
    "Category": ["Dining", "Rent", "Income"],
    "Amount": [250.0, 14000.0, 85000.0],
    "Type": ["Expense", "Expense", "Income"],
    "Description": ["Lunch", "Flat rent", "Salary"],
})


def save_twice_at_once(app, monkeypatch, with_token: bool) -> int:
    """Two saves of the same rows for one new user, each on its own connection. Each reads what is saved
    so far, then waits up to a second for the other: without a lock both read nothing and both insert."""
    with app.app_context():
        user_id = create_user(get_db(), f"u{uuid.uuid4().hex[:12]}", f"{uuid.uuid4().hex[:12]}@example.com", "correct-horse-battery")
    both_read = threading.Barrier(2, timeout=1)
    read = models.imported_signatures

    def read_then_wait(db, uid):
        seen = read(db, uid)
        try:
            both_read.wait()
        except threading.BrokenBarrierError:  # with the lock, the other save is still waiting for this one
            pass
        return seen

    monkeypatch.setattr(models, "imported_signatures", read_then_wait)
    errors = []

    def save():
        try:
            with app.app_context():
                db = get_db()
                if with_token:  # the review page's Save claims its one-time token first, in the same transaction
                    assert claim_import_token(db, user_id, uuid.uuid4().hex)
                save_transactions(db, user_id, ROWS, source_file="tx.csv")
        except Exception as exc:  # a thread's exception would otherwise vanish
            errors.append(exc)

    threads = [threading.Thread(target=save) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) AS n FROM transactions WHERE user_id = ?", (user_id,)).fetchone()["n"]


@pytest.mark.parametrize("with_token", [False, True], ids=["import", "review-save"])
def test_two_saves_at_once_import_the_rows_once(engine_app, monkeypatch, with_token):
    assert save_twice_at_once(engine_app, monkeypatch, with_token) == len(ROWS)
