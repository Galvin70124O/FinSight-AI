"""Goal routes: add, change, delete and a single-use Undo, each only on the signed-in user's own goals."""
from datetime import date

import blueprints.goals
from conftest import csrf_token, register_and_login
from models import get_goals

NEXT_YEAR = f"{date.today().year + 1}-06-01"


def add(client, name="Goa trip", target="60,000", when=NEXT_YEAR):
    return client.post("/goals/new", data={"name": name, "target": target, "target_date": when,
                                           "csrf_token": csrf_token(client, "/goals/new")})


def flashes(client) -> list[str]:
    with client.session_transaction() as session:
        return [message for _kind, message in session.pop("_flashes", [])]


def delete(client, goal_id: int) -> str:
    """Delete a goal; return the Undo token the dashboard would show."""
    client.post(f"/goals/{goal_id}/delete", data={"csrf_token": csrf_token(client, "/goals/new")})
    with client.session_transaction() as session:
        return session["undo_toast"]["token"]


def undo(client, token: str):
    return client.post("/goals/undo", data={"undo": token, "csrf_token": csrf_token(client, "/goals/new")})


def test_signed_out_goal_pages_send_you_to_sign_in(client):
    for response in (client.get("/goals/new", follow_redirects=True), client.post("/goals/undo", follow_redirects=True)):
        assert response.status_code == 200 and response.request.path == "/login"


def test_a_goal_is_added_changed_and_deleted(client, db, logged_in):
    response = add(client)
    assert response.status_code == 302 and response.headers["Location"].endswith("/dashboard#goals")
    [goal] = get_goals(db, logged_in)
    assert (goal["name"], goal["target_amount"], goal["target_date"]) == ("Goa trip", 60000.0, NEXT_YEAR)
    assert "Added Goa trip." in flashes(client)

    page = client.get(f"/goals/{goal['id']}/edit").get_data(as_text=True)
    assert 'value="Goa trip"' in page and 'value="60000"' in page
    client.post(f"/goals/{goal['id']}/edit", data={"name": "Goa, December", "target": "₹75,000.50", "target_date": NEXT_YEAR,
                                                   "csrf_token": csrf_token(client, "/goals/new")})
    assert [(g["name"], g["target_amount"]) for g in get_goals(db, logged_in)] == [("Goa, December", 75000.5)]

    delete(client, goal["id"])
    assert get_goals(db, logged_in) == []


def test_a_wrong_field_keeps_what_was_typed_and_says_what_is_wrong(client, db, logged_in):
    response = add(client, name="Bike", target="lots", when="")
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    assert 'value="Bike"' in page and 'value="lots"' in page
    assert "Enter the target in rupees" in page and "Pick a target date." in page
    assert 'aria-invalid=true' in page and get_goals(db, logged_in) == []


def test_undo_puts_a_deleted_goal_back_once(client, db, logged_in):
    add(client)
    token = delete(client, get_goals(db, logged_in)[0]["id"])
    undo(client, token)
    assert "Undone: Goa trip is back." in flashes(client)
    undo(client, token)
    assert "Goa trip is already back." in flashes(client)
    assert [g["name"] for g in get_goals(db, logged_in)] == ["Goa trip"]


def test_an_expired_undo_is_too_late(client, db, logged_in, monkeypatch):
    add(client)
    token = delete(client, get_goals(db, logged_in)[0]["id"])
    monkeypatch.setattr(blueprints.goals, "UNDO_MAX_AGE", -1)
    undo(client, token)
    assert any(m.startswith("Too late to undo") for m in flashes(client))
    assert get_goals(db, logged_in) == []


def test_another_users_undo_is_refused(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, ravi_id = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")
    add(asha)
    token = delete(asha, get_goals(db, asha_id)[0]["id"])
    undo(ravi, token)
    assert "That Undo belongs to another account, so nothing changed." in flashes(ravi)
    assert get_goals(db, asha_id) == [] and get_goals(db, ravi_id) == []


def test_goal_changes_without_a_csrf_token_are_refused(client, db, logged_in):
    add(client)
    [goal] = get_goals(db, logged_in)
    for path, form in [("/goals/new", {"name": "Bike", "target": "1000", "target_date": NEXT_YEAR}),
                       (f"/goals/{goal['id']}/edit", {"name": "Bike", "target": "1000", "target_date": NEXT_YEAR}),
                       (f"/goals/{goal['id']}/delete", {}),
                       ("/goals/undo", {"undo": "x"})]:
        response = client.post(path, data=form)
        assert response.status_code == 302, path
    assert get_goals(db, logged_in) == [goal]


def test_another_users_goal_is_not_found(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id = register_and_login(asha, "asha")
    register_and_login(ravi, "ravi")
    add(asha)
    [goal] = get_goals(db, asha_id)
    token = csrf_token(ravi, "/goals/new")
    assert ravi.get(f"/goals/{goal['id']}/edit").status_code == 404
    assert ravi.post(f"/goals/{goal['id']}/edit", data={"name": "Mine", "target": "1", "target_date": NEXT_YEAR,
                                                       "csrf_token": token}).status_code == 404
    assert ravi.post(f"/goals/{goal['id']}/delete", data={"csrf_token": token}).status_code == 404
    assert get_goals(db, asha_id) == [goal]
