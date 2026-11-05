"""Limits kept in the database: 5 failed sign-ins per username and 30 per address in 15 minutes,
5 sign-ups per username in 15 minutes and 10 per address in an hour (a hostel shares one address)."""
from datetime import datetime, timedelta, timezone

from conftest import csrf_token


def login(client, username, password, ip):
    return client.post("/login", data={"username": username, "password": password, "csrf_token": csrf_token(client, "/login")},
                       environ_base={"REMOTE_ADDR": ip})


def register(client, username, ip):
    data = {"username": username, "email": f"{username}@example.com", "password": "too-short", "confirm_password": "too-short",
            "csrf_token": csrf_token(client, "/register")}
    return client.post("/register", data=data, environ_base={"REMOTE_ADDR": ip})


def back_date(db, minutes):
    old = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="microseconds")
    db.execute("UPDATE auth_attempts SET attempted_at = ?", (old,))
    db.commit()


def signed_in(client) -> bool:
    with client.session_transaction() as session:
        return "user_id" in session


def test_five_wrong_passwords_lock_the_username_from_every_address_for_15_minutes(client, db, user_id):
    for i in range(5):
        assert "Incorrect username or password." in login(client, "asha", "wrong", f"203.0.113.{i}").get_data(as_text=True)
    refused = login(client, "asha", "secret123", "198.51.100.7")  # the right password, from a new address
    assert refused.status_code == 429 and "Too many attempts. Try again in 15 minutes." in refused.get_data(as_text=True)
    assert not signed_in(client)

    back_date(db, minutes=16)
    assert login(client, "asha", "secret123", "198.51.100.7").status_code == 302 and signed_in(client)
    assert db.execute("SELECT COUNT(*) FROM auth_attempts").fetchone()[0] == 0  # the privacy page says 15 minutes


def test_thirty_failures_from_one_address_lock_it_for_every_username(client, db, user_id):
    for i in range(29):  # usernames that don't exist count too
        login(client, f"guess{i}", "wrong", "203.0.113.9")
    assert login(client, "asha", "secret123", "203.0.113.9").status_code == 302  # 29 others failed on this network
    client.post("/logout", data={"csrf_token": csrf_token(client, "/dashboard")})
    login(client, "guess29", "wrong", "203.0.113.9")
    assert login(client, "asha", "secret123", "203.0.113.9").status_code == 429 and not signed_in(client)
    assert login(client, "asha", "secret123", "198.51.100.7").status_code == 302 and signed_in(client)


def test_signing_in_clears_the_usernames_count(client, db, user_id):
    for i in range(4):
        login(client, "asha", "wrong", f"203.0.113.{i}")
    assert login(client, "asha", "secret123", "198.51.100.1").status_code == 302
    for i in range(4):
        login(client, "asha", "wrong", f"203.0.113.{10 + i}")
    assert login(client, "asha", "secret123", "198.51.100.2").status_code == 302  # 8 failures, but 4 since signing in


def test_sign_ups_are_limited_per_address_for_an_hour_and_per_username(client, db):
    for i in range(10):
        register(client, f"user{i}", "203.0.113.5")
    refused = register(client, "someone", "203.0.113.5")
    assert refused.status_code == 429 and "Too many attempts. Try again in 60 minutes." in refused.get_data(as_text=True)
    back_date(db, minutes=16)
    assert register(client, "someone", "203.0.113.5").status_code == 429  # an address's sign-ups count for an hour
    back_date(db, minutes=61)
    assert register(client, "someone", "203.0.113.5").status_code == 200
    for i in range(5):
        register(client, "taken", f"198.51.100.{i}")
    assert register(client, "taken", "198.51.100.99").status_code == 429
    assert register(client, "fresh", "192.0.2.1").status_code == 200  # another name from another address


def test_failed_sign_ups_never_lock_anyone_out_of_signing_in(client, db, user_id):
    for i in range(5):
        register(client, "asha", f"203.0.113.{i}")
    assert login(client, "asha", "secret123", "203.0.113.0").status_code == 302


def test_in_production_visitors_behind_the_same_proxy_have_their_own_counts(app, client, db, user_id):
    app.config["IS_PRODUCTION"] = True  # every request arrives from Render's proxy, one shared address
    behind = {"REMOTE_ADDR": "10.0.0.1"}

    def login_via(headers, username, password):
        return client.post("/login", data={"username": username, "password": password, "csrf_token": csrf_token(client, "/login")},
                           headers=headers, environ_base=behind)

    for i in range(30):
        login_via({"X-Forwarded-For": "203.0.113.1"}, f"guess{i}", "wrong")
    assert login_via({"X-Forwarded-For": "198.51.100.2"}, "asha", "secret123").status_code == 302  # someone else

    for i in range(30):
        login_via({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": f"192.0.2.{i}"}, f"guess{i}", "wrong")
    refused = login_via({"CF-Connecting-IP": "203.0.113.7", "X-Forwarded-For": "192.0.2.99"}, "asha", "secret123")
    assert refused.status_code == 429  # Cloudflare's header wins over one the visitor can write
