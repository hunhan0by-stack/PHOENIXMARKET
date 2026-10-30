"""itemsatis payment links: only https links on itemsatis.com / www.itemsatis.com are saved or shown."""
import pytest

import services.config as config_checks
import services.payments as payments
import services.store as store
from app import app
from services.database import get_db
from services.itemsatis import is_valid_link, link_problem
from tests.helpers import ITEMSATIS_URL, in_app

INSTAGRAM, CANVA = 2, 1
GOOD_LINK = "https://www.itemsatis.com/ilan/instagram-takipci-587771.html"

ACCEPTED = [
    GOOD_LINK,
    "https://itemsatis.com/ilan/instagram-takipci-587771.html",
    "https://www.itemsatis.com/",
    "https://www.itemsatis.com",
    "https://www.itemsatis.com/magaza/phoenixmarket?sort=new#ilanlar",
    "HTTPS://WWW.ITEMSATIS.COM/ilan/x",  # scheme and host are case-insensitive
]

REJECTED = [
    "",
    "javascript:alert(1)",
    "JaVaScRiPt:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "//www.itemsatis.com/ilan/x",  # protocol-relative
    "www.itemsatis.com/ilan/x",  # no scheme
    "http://www.itemsatis.com/ilan/x",  # not https
    "ftp://www.itemsatis.com/x",
    "https://itemsatis.com.evil.example/ilan/x",  # look-alike: different site
    "https://evil.example/www.itemsatis.com/ilan/x",
    "https://evilitemsatis.com/ilan/x",
    "https://en.itemsatis.com/ilan/x",  # other subdomain
    "https://itemsatis.com./ilan/x",  # trailing dot
    "https://user:pass@www.itemsatis.com/ilan/x",  # credentials
    "https://www.itemsatis.com@evil.example/ilan/x",  # goes to evil.example
    "https://www.itemsatis.com:8443/ilan/x",  # port
    "https://www.itemsatis.com:443/ilan/x",
    "https://www.itemsatis.com:/ilan/x",
    "https://xn--itemsats-n2a.com/ilan/x",  # punycode look-alike
    "https://itemsatış.com/ilan/x",  # Unicode look-alike
    "https://www.itemsatis.com/ilan/x​",  # invisible character
    "https://www.itemsat%69s.com/ilan/x",  # encoded host
    "https://www.itemsatis.com\\@evil.example/",  # backslash
    "https:\\\\evil.example\\www.itemsatis.com",
    "https://www.itemsatis.com/ilan/x y",  # space
    " https://www.itemsatis.com/ilan/x",
    "https://www.itemsatis.com/ilan/x\n",
    "https://www.itemsatis.com/redirect?url=https://evil.example",  # hidden second address
    "https://www.itemsatis.com/redirect?url=https%3A%2F%2Fevil.example",
    "https://www.itemsatis.com/redirect?url=https%253A%252F%252Fevil.example",  # double-encoded
    "https://www.itemsatis.com/go?to=//evil.example",
    "https://www.itemsatis.com//evil.example",
    "https://www.itemsatis.com/ilan/x%0d%0aSet-Cookie:a=b",  # encoded control characters
    "https://www.itemsatis.com/ilan/%40evil.example",
    "https://www.itemsatis.com/" + "a" * 500,  # too long
    "https:///www.itemsatis.com/ilan/x",
    "https:www.itemsatis.com/ilan/x",
    "https://[::1]/ilan/x",
    None,
]


@pytest.mark.parametrize("url", ACCEPTED)
def test_itemsatis_links_are_accepted(url):
    assert link_problem(url) is None and is_valid_link(url)


@pytest.mark.parametrize("url", REJECTED)
def test_unsafe_or_foreign_links_are_rejected(url):
    assert link_problem(url) is not None and not is_valid_link(url)


# --- admin forms ------------------------------------------------------------------
def listing_form(product, url):
    return {
        "name": product["name"], "description": product["description"],
        "base_price": str(product["base_price"]), "base_currency": "USD",
        "listing_language": "en", "category": product["category"],
        "stock": str(product["stock"]), "status": "active", "payment_url": url,
    }


@pytest.mark.parametrize("url", [
    "http://www.itemsatis.com/ilan/x",
    "https://www.itemsatis.com@evil.example/x",
    "https://itemsatis.com.evil.example/x",
    "https://www.itemsatis.com:8443/x",
])
def test_listing_and_package_forms_refuse_unsafe_links(admin, url):
    product = in_app(store.get_product, INSTAGRAM)
    r = admin.post(f"/admin/listings/{INSTAGRAM}/edit", listing_form(product, url))
    assert "must start with https://www.itemsatis.com/" in r.get_data(as_text=True)
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] is None
    admin.post(f"/admin/listings/{INSTAGRAM}/packages", {
        "name": "1K", "price": "4.99", "stock": "5", "sort_order": "0", "active": "1", "payment_url": url,
    })
    assert in_app(store.list_variants, INSTAGRAM) == []


def test_listing_form_saves_an_itemsatis_link(admin):
    product = in_app(store.get_product, INSTAGRAM)
    admin.post(f"/admin/listings/{INSTAGRAM}/edit", listing_form(product, GOOD_LINK))
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] == GOOD_LINK


@pytest.mark.parametrize("url", [
    "http://www.itemsatis.com/magaza/x",
    "https://www.itemsatis.com@evil.example/",
    "https://itemsatis.com.evil.example/",
    "https://",
    "https://www.itemsatis.com/x y",
])
def test_site_wide_link_refuses_unsafe_links_and_keeps_the_old_one(admin, url):
    admin.post("/admin/settings", {"itemsatis_redirect_url": url, "support_email": "", "support_discord": ""})
    assert any("must start with https://www.itemsatis.com/" in m for m in admin.flashes())
    assert in_app(store.get_config, "site_settings.itemsatis_redirect_url") == ITEMSATIS_URL
    assert app.config["ITEMSATIS_REDIRECT_URL"] == ITEMSATIS_URL


def test_site_wide_link_accepts_an_itemsatis_link(admin, monkeypatch):
    monkeypatch.setitem(app.config, "ITEMSATIS_REDIRECT_URL", ITEMSATIS_URL)
    new = "https://itemsatis.com/magaza/phoenixmarket"
    admin.post("/admin/settings", {"itemsatis_redirect_url": new, "support_email": "", "support_discord": ""})
    assert in_app(store.get_config, "site_settings.itemsatis_redirect_url") == new
    assert app.config["ITEMSATIS_REDIRECT_URL"] == new


# --- links saved before the check existed -----------------------------------------
def save_link_directly(table, row_id, url):
    """Simulate a link saved by an older version (bypasses the form check)."""
    def _save():
        db = get_db()
        db.execute(f"UPDATE {table} SET payment_url = ? WHERE id = ?", (url, row_id))
        db.commit()
    in_app(_save)


def place_order(client, product_id=INSTAGRAM):
    client.post(f"/cart/add/{product_id}")
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    return in_app(store.list_orders_admin)[0]


def test_an_old_invalid_listing_link_is_never_shown_and_never_deleted(client):
    bad = "https://www.itemsatis.com@evil.example/ilan"
    save_link_directly("products", INSTAGRAM, bad)
    order = place_order(client)
    token = in_app(store.get_order_by_id, order["id"])["tracking_token"]
    for page in (f"/checkout/complete/{token}", f"/track/{token}"):
        html = client.html(page)
        assert "evil.example" not in html
        assert f'href="{ITEMSATIS_URL}"' in html  # falls back to the site-wide link, as an empty one does
    emails = [e for e in in_app(store.list_emails, 100) if e["kind"] == "order_confirmation"]
    assert emails and "evil.example" not in emails[0]["body_text"]
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] == bad  # record kept as it was


def test_an_invalid_site_wide_link_is_not_shown(client, monkeypatch):
    monkeypatch.setitem(app.config, "ITEMSATIS_REDIRECT_URL", "http://itemsatis.com.evil.example/")
    save_link_directly("products", CANVA, GOOD_LINK)
    client.post(f"/cart/add/{INSTAGRAM}")
    client.post(f"/cart/add/{CANVA}")
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    order = in_app(store.get_order_by_id, in_app(store.list_orders_admin)[0]["id"])
    links = in_app_request(lambda: payments.payment_links(order))
    assert links == [{"url": GOOD_LINK, "names": ["Canva Pro Subscription"]}]
    assert "evil.example" not in client.html(f"/checkout/complete/{order['tracking_token']}")


def in_app_request(fn):
    with app.test_request_context():
        return fn()


def test_saved_link_report_lists_invalid_links_without_changing_them(monkeypatch):
    save_link_directly("products", INSTAGRAM, "http://www.itemsatis.com/ilan/x")
    in_app(store.set_config, "site_settings.itemsatis_redirect_url", "https://example.com/itemsatis")
    found = in_app(payments.saved_link_problems)
    assert [f["problem"] for f in found] == ["not itemsatis.com or www.itemsatis.com", "not https"]
    assert found[1]["where"].startswith(f"Listing #{INSTAGRAM} ")

    result = app.test_cli_runner().invoke(args=["check-payment-links"])
    assert result.exit_code == 1
    assert "2 link(s) need correcting" in result.output and "Nothing was changed" in result.output
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] == "http://www.itemsatis.com/ilan/x"
    assert in_app(store.get_config, "site_settings.itemsatis_redirect_url") == "https://example.com/itemsatis"


def test_saved_link_report_is_clean_for_valid_links():
    save_link_directly("products", INSTAGRAM, GOOD_LINK)
    assert in_app(payments.saved_link_problems) == []
    result = app.test_cli_runner().invoke(args=["check-payment-links"])
    assert result.exit_code == 0 and "All saved itemsatis links pass" in result.output


# --- production start-up ----------------------------------------------------------
GOOD_ENV = {
    "DATABASE_PATH": "/var/data/phoenixmarket.db",
    "ADMIN_PASSWORD": "a-long-admin-pass",
    "SECRET_KEY": "x" * 48,
}


@pytest.mark.parametrize("url", ["", "https://example.com/itemsatis", "http://www.itemsatis.com/x", "https://www.itemsatis.com@evil.example/"])
def test_production_refuses_an_unsafe_itemsatis_setting(url):
    problems = config_checks.production_problems({**GOOD_ENV, "ITEMSATIS_REDIRECT_URL": url}, on_render=True)
    assert any("ITEMSATIS_REDIRECT_URL" in p for p in problems)


def test_production_accepts_an_itemsatis_setting():
    env = {**GOOD_ENV, "ITEMSATIS_REDIRECT_URL": "https://www.itemsatis.com/magaza/phoenixmarket"}
    assert config_checks.production_problems(env, on_render=True) == []
