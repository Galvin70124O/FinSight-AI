import io
import re
import zlib

from conftest import csrf_token, register_and_login

STATEMENT = (
    b"Date,Category,Amount,Type,Description\n"
    b"2026-03-01,Dining,450,Expense,UPI/ZOMATO LTD/412345678901/Paid via PhonePe\n"
    b"2026-03-02,Dining,320,Expense,UPI/ZOMATO LTD/412345678902/Paid via PhonePe\n"
    b"2026-03-03,Pet care,800,Expense,UPI/HAPPY PAWS CLINIC/412345678903\n"
    b"2026-03-04,Uncategorized,1500,Expense,UPI/RAHUL S/412345678904\n"
)


def import_statement(client, content=STATEMENT):
    client.post("/upload", data={"file": (io.BytesIO(content), "s.csv"), "csrf_token": csrf_token(client, "/upload")},
                content_type="multipart/form-data")


def tx_id(db, description_start, user_id):
    return db.execute("SELECT id FROM transactions WHERE user_id = ? AND description LIKE ? ORDER BY date LIMIT 1",
                      (user_id, description_start + "%")).fetchone()[0]


def categories(db, user_id):
    return [tuple(r) for r in db.execute("SELECT description, category FROM transactions WHERE user_id = ? ORDER BY date", (user_id,))]


def change_row(client, transaction_id, category, token=True):
    data = {"category": category}
    if token:
        data["csrf_token"] = csrf_token(client, "/dashboard")
    return client.post(f"/transactions/{transaction_id}/category", data=data, follow_redirects=True)


def test_one_row_changes_alone_and_is_not_remembered(client, db, logged_in):
    import_statement(client)
    page = change_row(client, tx_id(db, "UPI/ZOMATO LTD/412345678901", logged_in), "Groceries").get_data(as_text=True)

    assert "Changed one transaction to Groceries." in page
    assert categories(db, logged_in)[:2] == [("UPI/ZOMATO LTD/412345678901/Paid via PhonePe", "Groceries"),
                                             ("UPI/ZOMATO LTD/412345678902/Paid via PhonePe", "Dining")]
    assert db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


def test_the_overview_offers_the_change_with_the_current_category_selected(client, db, logged_in):
    import_statement(client)
    page = client.get("/dashboard").get_data(as_text=True)

    def form(description):
        return re.search(rf'action="/transactions/{tx_id(db, description, logged_in)}/category".*?</form>', page, re.S).group(0)

    assert "<option selected>Dining</option>" in form("UPI/ZOMATO LTD/412345678901")
    assert "<option selected>Pet care</option>" in form("UPI/HAPPY PAWS")  # the user's own category
    assert "selected" not in form("UPI/RAHUL S")  # Uncategorized: "Choose a category" shows


def test_another_users_row_cannot_be_changed(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, _ = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")
    import_statement(asha)
    before = categories(db, asha_id)

    response = change_row(ravi, tx_id(db, "UPI/ZOMATO LTD", asha_id), "Groceries")

    assert response.status_code == 404
    assert categories(db, asha_id) == before


def test_an_unknown_or_blank_category_is_rejected(client, db, logged_in):
    import_statement(client)
    before, row = categories(db, logged_in), tx_id(db, "UPI/ZOMATO LTD", logged_in)

    for value in ("<b>Free money</b>", "", "Uncategorized"):
        assert "Choose a category from the list" in change_row(client, row, value).get_data(as_text=True)
    assert categories(db, logged_in) == before


def test_a_change_without_a_csrf_token_changes_nothing(client, db, logged_in):
    import_statement(client)
    before = categories(db, logged_in)
    response = change_row(client, tx_id(db, "UPI/ZOMATO LTD", logged_in), "Groceries", token=False)
    assert response.status_code == 200 and categories(db, logged_in) == before


def add(db, user_id, description, category="Uncategorized", amount=100, date="2026-03-01", kind="Expense"):
    db.execute("INSERT INTO transactions (user_id, date, category, amount, type, description) VALUES (?, ?, ?, ?, ?, ?)",
               (user_id, date, category, amount, kind, description))
    db.commit()


def merchant_order(page):
    return re.findall(r'<b id="m[0-9a-f]+-name">([^<]+)</b>', page)


def change_merchant(client, merchant, category, **extra):
    data = {"merchant": merchant, "category": category, "csrf_token": csrf_token(client, "/categories"), **extra}
    return client.post("/categories", data=data, follow_redirects=True)


def test_merchants_are_listed_uncategorized_first_and_searchable(client, db, logged_in):
    add(db, logged_in, "UPI/ZOMATO LTD/412345678901", "Dining", 900)
    add(db, logged_in, "UPI/RAHUL S/412345678902", "Uncategorized", 50)
    add(db, logged_in, "UPI/BLINKIT/412345678903", "Groceries", 300)

    page = client.get("/categories").get_data(as_text=True)
    assert merchant_order(page) == ["RAHUL S", "ZOMATO", "BLINKIT"]  # Uncategorized first, then by amount
    assert "1 merchant with Uncategorized transactions" in page and "₹900" in page

    assert merchant_order(client.get("/categories?q=grocer").get_data(as_text=True)) == ["BLINKIT"]
    assert "No merchant or category matches" in client.get("/categories?q=nothing-like-this").get_data(as_text=True)


def test_changing_a_merchant_changes_all_its_rows_and_is_remembered_for_that_user_only(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, ravi_id = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")
    for user in (asha_id, ravi_id):
        add(db, user, "UPI/ZOMATO LTD/412345678901", "Dining")
        add(db, user, "UPI/ZOMATO LTD/412345678902", "Uncategorized", date="2026-03-02")
    add(db, asha_id, "UPI/BLINKIT/412345678903", "Groceries")

    page = change_merchant(asha, "ZOMATO", "Groceries").get_data(as_text=True)

    assert "ZOMATO: 2 transactions now in Groceries." in page
    assert [c for _, c in categories(db, asha_id)] == ["Groceries", "Groceries", "Groceries"]
    assert [c for _, c in categories(db, ravi_id)] == ["Dining", "Uncategorized"]
    assert [tuple(r) for r in db.execute("SELECT user_id, merchant_key, category FROM category_overrides")] == [(asha_id, "ZOMATO", "Groceries")]


def test_a_merchant_the_user_does_not_have_changes_nothing_and_saves_no_correction(app, db):
    asha, ravi = app.test_client(), app.test_client()
    asha_id, _ = register_and_login(asha, "asha"), register_and_login(ravi, "ravi")
    add(db, asha_id, "UPI/ZOMATO LTD/412345678901", "Dining")

    assert "not in your transactions" in change_merchant(ravi, "ZOMATO", "Groceries").get_data(as_text=True)
    assert categories(db, asha_id) == [("UPI/ZOMATO LTD/412345678901", "Dining")]
    assert db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


def test_an_unknown_or_blank_category_for_a_merchant_is_rejected(client, db, logged_in):
    add(db, logged_in, "UPI/ZOMATO LTD/412345678901", "Dining")
    for value in ("Free money", "", "Uncategorized"):
        assert "Choose a category from the list" in change_merchant(client, "ZOMATO", value).get_data(as_text=True)
    assert categories(db, logged_in) == [("UPI/ZOMATO LTD/412345678901", "Dining")]
    assert db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


def test_a_merchant_change_without_a_csrf_token_changes_nothing(client, db, logged_in):
    add(db, logged_in, "UPI/ZOMATO LTD/412345678901", "Dining")
    response = client.post("/categories", data={"merchant": "ZOMATO", "category": "Groceries"}, follow_redirects=True)
    assert response.status_code == 200 and "Categories" in response.get_data(as_text=True)
    assert categories(db, logged_in) == [("UPI/ZOMATO LTD/412345678901", "Dining")]


def test_one_row_changed_from_the_categories_page_changes_alone_and_comes_back_open(client, db, logged_in):
    add(db, logged_in, "UPI/ZOMATO LTD/412345678901", "Dining")
    add(db, logged_in, "UPI/ZOMATO LTD/412345678902", "Dining", date="2026-03-02")
    row = tx_id(db, "UPI/ZOMATO LTD/412345678901", logged_in)
    anchor = re.search(r'<li class="merchant cat-merchant" id="(m[0-9a-f]+)"', client.get("/categories").get_data(as_text=True)).group(1)

    response = client.post(f"/transactions/{row}/category", data={
        "category": "Groceries", "return_to": "categories", "anchor": anchor, "open": anchor, "q": "zom",
        "csrf_token": csrf_token(client, "/categories"),
    })

    assert response.headers["Location"] == f"/categories?q=zom&open={anchor}#{anchor}"
    assert [c for _, c in categories(db, logged_in)] == ["Groceries", "Dining"]
    assert db.execute("SELECT COUNT(*) FROM category_overrides").fetchone()[0] == 0


def test_merchants_with_uncategorized_rows_open_by_default_only_when_there_are_five_or_fewer(client, db, logged_in):
    names = ["RAHUL S", "ANITA K", "KAVYA NAIR", "ARJUN MENON", "PRIYA RAO", "DIVYA DAS"]
    for i, name in enumerate(names[:5]):
        add(db, logged_in, f"UPI/{name}/41234567890{i}")
    page = client.get("/categories").get_data(as_text=True)
    assert page.count('class="cat-rows"') == 5 and page.count("Hide transactions") == 5

    add(db, logged_in, f"UPI/{names[5]}/412345678909")
    page = client.get("/categories").get_data(as_text=True)
    assert 'class="cat-rows"' not in page and page.count("Show the transaction") == 6


def test_a_merchants_rows_show_twenty_at_a_time(client, db, logged_in):
    for i in range(45):
        add(db, logged_in, f"UPI/ZOMATO LTD/4123456789{i:02d}", "Dining", date=f"2026-0{1 + i % 9}-{1 + i % 28:02d}")
    page = client.get("/categories").get_data(as_text=True)
    anchor = re.search(r'id="(m[0-9a-f]+)"', page).group(1)
    assert "Show its 45 transactions" in page

    opened = client.get(f"/categories?open={anchor}").get_data(as_text=True)
    assert opened.count('class="cat-row"') == 20 and "Showing 20 of 45" in opened and "Show 20 more" in opened
    more = client.get(f"/categories?open={anchor}&more={anchor}&rows=40").get_data(as_text=True)
    assert more.count('class="cat-row"') == 40 and "Show 5 more" in more


def test_rows_without_a_merchant_name_have_no_merchant_wide_change(client, db, logged_in):
    add(db, logged_in, "412345678901")
    page = client.get("/categories").get_data(as_text=True)
    assert "No name in the description" in page and "change these one at a time" in page
    assert 'name="merchant" value=""' not in page


def test_the_categories_page_is_reachable_from_the_sidebar_palette_and_transactions(client, db, logged_in):
    add(db, logged_in, "UPI/ZOMATO LTD/412345678901", "Dining")
    overview = client.get("/dashboard").get_data(as_text=True)
    assert overview.count('href="/categories"') >= 2  # sidebar and the Transactions section
    assert '"label": "Categories"' in overview  # command palette
    assert 'nav-link active" href="/categories"' in client.get("/categories").get_data(as_text=True)


def test_the_result_of_a_change_is_shown_beside_what_changed(client, db, logged_in):
    add(db, logged_in, "UPI/ZOMATO LTD/412345678901", "Dining")
    page = change_merchant(client, "ZOMATO", "Groceries").get_data(as_text=True)
    merchant = re.search(r'<li class="merchant cat-merchant" id="m[0-9a-f]+".*?</li>', page, re.S).group(0)
    assert "now in Groceries" in merchant and 'class="flash-stack"' not in page

    overview = change_row(client, tx_id(db, "UPI/ZOMATO", logged_in), "Dining").get_data(as_text=True)
    section = overview[overview.index('id="transactions"'):]
    assert "Changed one transaction to Dining." in section.split("</table>")[0] and 'class="flash-stack"' not in overview


def test_a_change_made_while_searching_still_shows_its_merchant_and_result(client, db, logged_in):
    add(db, logged_in, "UPI/BLINKIT/412345678901", "Uncategorized")
    add(db, logged_in, "UPI/ZOMATO LTD/412345678902", "Uncategorized", date="2026-03-02")
    row = tx_id(db, "UPI/ZOMATO", logged_in)
    page = change_merchant(client, "BLINKIT", "Groceries", q="uncategorized").get_data(as_text=True)
    assert "BLINKIT: 1 transaction now in Groceries." in page  # no longer matches the search, but shown

    page = client.post(f"/transactions/{row}/category", data={
        "category": "Dining", "return_to": "categories", "anchor": f"m{zlib.crc32(b'ZOMATO'):x}",
        "q": "uncategorized", "csrf_token": csrf_token(client, "/categories"),
    }, follow_redirects=True).get_data(as_text=True)
    assert "Changed one transaction to Dining." in page
