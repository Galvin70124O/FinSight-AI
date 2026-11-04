import re

from conftest import csrf_token


def test_unknown_page_is_a_friendly_404_with_a_way_back(client):
    page = client.get("/no-such-page")
    html = page.get_data(as_text=True)

    assert page.status_code == 404
    assert "This page does not exist" in html
    assert 'href="/"' in html


def test_every_page_has_its_own_title_and_a_description(client, logged_in):
    client.post("/demo-data", data={"csrf_token": csrf_token(client, "/dashboard")})
    titles = set()
    for path in ("/dashboard", "/upload"):
        html = client.get(path).get_data(as_text=True)
        titles.add(re.search(r"<title>(.*?)</title>", html).group(1))
        assert re.search(r'<meta name="description" content="[^"]{40,}">', html)
    assert len(titles) == 2


def test_footer_year_comes_from_the_clock(client):
    from datetime import date

    assert f"© {date.today().year} FinSight AI" in client.get("/").get_data(as_text=True)
