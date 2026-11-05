import io
import random
import re
import threading
from urllib.parse import urlencode

import pytest

from conftest import csrf_token, register_and_login

NO_CATEGORY = (
    b"Date,Amount,Type,Description\n"
    b"2025-03-01,450,Expense,UPI/ZOMATO LTD/412345678901/Paid via PhonePe\n"
    b"2025-03-02,320,Expense,UPI/ZOMATO LTD/412345678902/Paid via PhonePe\n"
    b"2025-03-03,800,Expense,POS 4591XXXXXX1234 NEW HEALTH MEDICALS PUNE\n"
    b"2025-03-04,1500,Expense,UPI/RAHUL S/412345678903\n"
    b"2025-03-05,85000,Income,ACH/SALARY/ACME TECH\n"
)


def upload(client, content: bytes, follow=True):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(content), "statement.csv"), "csrf_token": csrf_token(client, "/upload")},
        content_type="multipart/form-data",
        follow_redirects=follow,
    )


def review_form(page: str) -> dict[str, str]:
    """The review form's fields with their selected values, as a browser would send them."""
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', page))
    for name, body in re.findall(r'<select[^>]*name="(group-\d+)"[^>]*>(.*?)</select>', page, re.S):
        selected = re.search(r'<option(?: value="([^"]*)")? selected>([^<]*)</option>', body)
        fields[name] = "" if selected is None else (selected.group(1) if selected.group(1) is not None else selected.group(2))
    # A browser sends a checkbox only when it is ticked.
    fields.update(re.findall(r'<input type="checkbox" name="(remember-\d+)" value="(1)" checked>', page))
    return fields


def group_of(page: str, merchant: str) -> str:
    return re.search(rf'<b id="m(\d+)">{re.escape(merchant)}</b>', page).group(1)


def rows(db, user_id):
    return [tuple(r) for r in db.execute("SELECT description, category FROM transactions WHERE user_id = ? ORDER BY date", (user_id,))]


def test_a_file_without_categories_is_reviewed_then_saved(client, db, logged_in):
    page = upload(client, NO_CATEGORY).get_data(as_text=True)

    assert "Check the categories" in page and rows(db, logged_in) == []  # nothing saved before Save
    assert page.index("RAHUL S") < page.index("NEW HEALTH MEDICALS") < page.index("ZOMATO")  # unknowns first
    assert "Keyword rule: ZOMATO" in page and "% sure" in page and "Needs a category" in page

    form = review_form(page)
    form[f"group-{group_of(page, 'RAHUL S')}"] = "Rent"  # a merchant that needed a category
    saved = client.post("/upload/save", data=form, follow_redirects=True).get_data(as_text=True)

    assert "Imported 5 transactions." in saved
    assert dict((d.split("/")[1] if "/" in d else d, c) for d, c in rows(db, logged_in)) == {
        "ZOMATO LTD": "Dining", "RAHUL S": "Rent", "SALARY": "Income",
        "POS 4591XXXXXX1234 NEW HEALTH MEDICALS PUNE": "Healthcare",
    }
    # RAHUL S is a person, and corrections for people are only remembered when ticked.
    assert db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("merchant", "tick", "remembered"),
    [
        ("RAHUL S", None, False),  # a person: unticked by default
        ("RAHUL S", True, True),  # a person, ticked
        ("NEW HEALTH MEDICALS", None, True),  # a business: ticked by default
        ("NEW HEALTH MEDICALS", False, False),  # a business, unticked
    ],
)
def test_only_ticked_changes_are_remembered(client, db, logged_in, merchant, tick, remembered):
    page = upload(client, NO_CATEGORY).get_data(as_text=True)
    group = group_of(page, merchant)
    assert (f'name="remember-{group}" value="1" checked' in page) == (merchant != "RAHUL S")
    label = "Remember for this person" if merchant == "RAHUL S" else "Remember for this merchant"
    assert label in page

    form = review_form(page)
    form[f"group-{group}"] = "Shopping"
    if tick is True:
        form[f"remember-{group}"] = "1"
    elif tick is False:
        form.pop(f"remember-{group}", None)
    client.post("/upload/save", data=form)

    saved = [tuple(r) for r in db.execute("SELECT merchant_key, category FROM category_overrides")]
    assert saved == ([(merchant, "Shopping")] if remembered else [])
    assert ("Shopping",) in [tuple(r)[1:] for r in rows(db, logged_in)]  # the rows change either way


def test_unchosen_merchants_are_saved_as_uncategorized_without_a_correction(client, db, logged_in):
    page = upload(client, NO_CATEGORY).get_data(as_text=True)
    saved = client.post("/upload/save", data=review_form(page), follow_redirects=True).get_data(as_text=True)

    assert "Imported 5 transactions. 1 row saved as Uncategorized." in saved
    assert ("UPI/RAHUL S/412345678903", "Uncategorized") in rows(db, logged_in)
    assert db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


def test_rows_with_their_own_category_keep_it_and_imported_rows_are_skipped(client, db, logged_in):
    upload(client, b"Date,Category,Amount,Type,Description\n2025-03-01,Food,450,Expense,UPI/ZOMATO LTD/412345678901/Paid via PhonePe\n")
    mixed = (b"Date,Category,Amount,Type,Description\n"
             b"2025-03-01,,450,Expense,UPI/ZOMATO LTD/412345678901/Paid via PhonePe\n"
             b"2025-03-06,Gifts,999,Expense,UPI/ANITA K/412345678909\n"
             b"2025-03-07,,250,Expense,UPI/BLINKIT/412345678910\n")
    page = upload(client, mixed).get_data(as_text=True)

    assert "1 row with a category in your file keeps it." in page
    assert "1 row imported before is skipped." in page
    assert "ZOMATO" not in page.split('<ol class="merchants">')[1]
    client.post("/upload/save", data=review_form(page))
    assert dict(rows(db, logged_in)) == {
        "UPI/ZOMATO LTD/412345678901/Paid via PhonePe": "Food",
        "UPI/ANITA K/412345678909": "Gifts",
        "UPI/BLINKIT/412345678910": "Groceries",
    }


def test_an_invalid_category_from_the_form_is_rejected(client, db, logged_in):
    page = upload(client, NO_CATEGORY).get_data(as_text=True)
    form = review_form(page)
    form[f"group-{group_of(page, 'ZOMATO')}"] = "<script>alert(1)</script>"

    result = client.post("/upload/save", data=form, follow_redirects=True).get_data(as_text=True)

    assert "not on the list, so nothing was saved" in result
    assert rows(db, logged_in) == [] and db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


def test_a_changed_or_foreign_payload_is_rejected(app, client, db, logged_in):
    page = upload(client, NO_CATEGORY).get_data(as_text=True)
    tampered = review_form(page)
    tampered["payload"] = tampered["payload"][:-3] + "abc"
    assert "expired or could not be read" in client.post("/upload/save", data=tampered, follow_redirects=True).get_data(as_text=True)

    other = app.test_client()
    other_id = register_and_login(other, "ravi")
    stolen = review_form(page)
    stolen["csrf_token"] = csrf_token(other, "/upload")
    assert "expired or could not be read" in other.post("/upload/save", data=stolen, follow_redirects=True).get_data(as_text=True)
    assert rows(db, logged_in) == [] and rows(db, other_id) == []


def test_an_expired_review_is_rejected(client, db, logged_in, monkeypatch):
    import blueprints.main

    page = upload(client, NO_CATEGORY).get_data(as_text=True)
    monkeypatch.setattr(blueprints.main, "REVIEW_MAX_AGE", -1)
    assert "expired or could not be read" in client.post("/upload/save", data=review_form(page), follow_redirects=True).get_data(as_text=True)
    assert rows(db, logged_in) == []


def test_saving_twice_imports_once(client, db, logged_in):
    form = review_form(upload(client, NO_CATEGORY).get_data(as_text=True))

    first = client.post("/upload/save", data=form, follow_redirects=True).get_data(as_text=True)
    again = client.post("/upload/save", data=form, follow_redirects=True).get_data(as_text=True)

    assert "Imported 5 transactions." in first
    assert "already saved, so nothing was imported twice" in again
    assert len(rows(db, logged_in)) == 5


def test_a_double_tap_sent_at_the_same_moment_imports_once(app, client, db, logged_in):
    form = review_form(upload(client, NO_CATEGORY).get_data(as_text=True))
    cookie = client.get_cookie("session").value
    taps = []
    for _ in range(2):
        tap = app.test_client()
        tap.set_cookie("session", cookie)
        taps.append(tap)
    start, results = threading.Barrier(2), []

    def send(tap):
        start.wait()
        results.append(tap.post("/upload/save", data=form).headers["Location"])

    threads = [threading.Thread(target=send, args=(tap,)) for tap in taps]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(rows(db, logged_in)) == 5
    assert db.execute("SELECT COUNT(*) FROM import_tokens").fetchone()[0] == 1
    assert sorted(results) == ["/dashboard", "/dashboard"]


def test_one_users_corrections_never_reach_another(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, ravi_id = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")

    page = upload(asha, NO_CATEGORY).get_data(as_text=True)
    form = review_form(page)
    form[f"group-{group_of(page, 'ZOMATO')}"] = "Shopping"
    asha.post("/upload/save", data=form)

    later = NO_CATEGORY.replace(b"2025-03", b"2025-04")
    assert "Your earlier correction" in upload(asha, later).get_data(as_text=True)
    ravi_page = upload(ravi, later).get_data(as_text=True)
    assert "Your earlier correction" not in ravi_page and "Keyword rule: ZOMATO" in ravi_page
    ravi.post("/upload/save", data=review_form(ravi_page))
    assert dict(rows(db, ravi_id))["UPI/ZOMATO LTD/412345678901/Paid via PhonePe"] == "Dining"
    assert dict(rows(db, asha_id))["UPI/ZOMATO LTD/412345678901/Paid via PhonePe"] == "Shopping"


def test_the_largest_accepted_file_round_trips_under_the_request_limit(app, client, db, logged_in):
    """A statement just under 2 MB with no Category column: about 31,000 rows, 300 merchants and a
    unique reference on every row (the worst case for compressing the review payload)."""
    rng = random.Random(1)
    names = [f"{a} {b}" for a in ("SRI LAKSHMI", "NEW", "ROYAL", "CITY", "GREEN", "SUNRISE", "GANESH", "ANNAPURNA", "RAJ", "STAR")
             for b in ("TIFFINS", "MEDICALS", "STORES", "TRAVELS", "BAKERY", "FASHION", "ELECTRONICS", "DAIRY", "CHEMISTS", "OPTICALS",
                       "TAILORS", "SWEETS", "MOTORS", "AGENCIES", "TRADERS", "PHOTO STUDIO", "FURNITURE", "HARDWARE", "BOOK HOUSE",
                       "FLOWERS", "JEWELLERS", "LAUNDRY", "SALON", "GYM", "MOBILES", "CANTEEN", "PAN SHOP", "FRUITS", "TEA STALL", "PRINTERS")]
    lines, size = ["Date,Amount,Type,Description"], 30
    while True:
        line = (f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d},{rng.randint(20, 5000)}.{rng.randint(0, 99):02d},Expense,"
                f"UPI/{rng.choice(names)}/{rng.randint(10 ** 11, 10 ** 12 - 1)}/{rng.choice(['Paid via PhonePe', 'UPI', ''])}")
        if size + len(line) + 1 > 2_090_000:
            break
        lines.append(line)
        size += len(line) + 1

    page = upload(client, ("\n".join(lines) + "\n").encode()).get_data(as_text=True)
    assert "Check the categories" in page
    assert '<form method="post" action="/upload/save" class="review-form" data-once>' in page  # URL-encoded: see the template
    form = review_form(page)
    request_size = len(urlencode(form))
    print(f"\n{len(lines) - 1} rows: payload {len(form['payload']):,} B, Save request {request_size:,} B of {app.config['MAX_CONTENT_LENGTH']:,}")
    assert request_size < app.config["MAX_CONTENT_LENGTH"]

    assert f"Imported {len(lines) - 1:,} transactions." in client.post("/upload/save", data=form, follow_redirects=True).get_data(as_text=True)
    assert db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == len(lines) - 1
