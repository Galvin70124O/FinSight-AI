"""Recurring routes: dismiss and restore, only ever on the signed-in user's own list."""
from conftest import csrf_token, register_and_login
from models import get_dismissed

NETFLIX = {"payee": "NETFLIX", "period_days": "30", "amount_paise": "64900"}


def post(client, path: str, form: dict):
    return client.post(path, data={**form, "csrf_token": csrf_token(client, "/goals/new")})


def test_signed_out_dismiss_sends_you_to_sign_in(client):
    response = client.post("/recurring/dismiss", data=NETFLIX, follow_redirects=True)
    assert response.request.path == "/login"


def test_a_payment_is_dismissed_and_restored(client, db, logged_in):
    response = post(client, "/recurring/dismiss", NETFLIX)
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard#recurring")
    assert get_dismissed(db, logged_in) == {("NETFLIX", 30, 64900)}
    post(client, "/recurring/dismiss", NETFLIX)  # twice is harmless
    assert get_dismissed(db, logged_in) == {("NETFLIX", 30, 64900)}
    post(client, "/recurring/restore", NETFLIX)
    assert get_dismissed(db, logged_in) == set()


def test_a_form_no_button_sends_is_refused(client, db, logged_in):
    for bad in ({**NETFLIX, "payee": " "}, {**NETFLIX, "payee": "X" * 201}, {**NETFLIX, "period_days": "10"},
                {**NETFLIX, "amount_paise": "0"}, {**NETFLIX, "amount_paise": "6.49"}, {"payee": "NETFLIX"}):
        assert post(client, "/recurring/dismiss", bad).status_code == 400, bad
    assert get_dismissed(db, logged_in) == set()


def test_dismiss_and_restore_without_a_csrf_token_are_refused(client, db, logged_in):
    assert client.post("/recurring/dismiss", data=NETFLIX).status_code == 302
    assert get_dismissed(db, logged_in) == set()
    post(client, "/recurring/dismiss", NETFLIX)
    assert client.post("/recurring/restore", data=NETFLIX).status_code == 302
    assert get_dismissed(db, logged_in) == {("NETFLIX", 30, 64900)}


def test_one_users_dismissals_are_out_of_another_users_reach(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, ravi_id = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")
    post(asha, "/recurring/dismiss", NETFLIX)
    assert get_dismissed(db, ravi_id) == set()
    post(ravi, "/recurring/restore", NETFLIX)  # Ravi can't undo Asha's choice
    assert get_dismissed(db, asha_id) == {("NETFLIX", 30, 64900)}
    post(ravi, "/recurring/dismiss", {**NETFLIX, "payee": "SPOTIFY"})
    assert get_dismissed(db, asha_id) == {("NETFLIX", 30, 64900)}
