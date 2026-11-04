import io
import re

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
