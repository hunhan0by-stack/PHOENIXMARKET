"""Every page renders in both languages; canonical URLs and the mobile menu."""
import re

import pytest

from app import app

PUBLIC = [
    "/", "/products", "/search?q=canva", "/category/design", "/product/canva-pro-subscription",
    "/product/1", "/cart", "/contact", "/signin", "/signup", "/forgot-password",
    "/sitemap.xml", "/health",
]
ADMIN = [
    "/admin/dashboard", "/admin/listings", "/admin/listings/new", "/admin/categories",
    "/admin/edit-category/1", "/admin/content", "/admin/settings", "/admin/orders",
    "/admin/orders.csv", "/admin/listings/1/edit", "/admin/coupons", "/admin/reviews",
    "/admin/reviews?status=hidden", "/admin/emails", "/admin/messages", "/admin/messages?status=all",
]


def failing_pages(client, paths):
    codes = {p: client.get(p).status_code for p in paths}
    return {p: code for p, code in codes.items() if code != 200}


@pytest.mark.parametrize("locale", ["en", "tr"])
def test_public_pages_render(make_client, locale):
    assert not failing_pages(make_client(locale=locale), PUBLIC)


@pytest.mark.parametrize("locale", ["en", "tr"])
def test_admin_pages_render(make_client, locale):
    c = make_client(locale=locale, ip="10.8.0.1")
    c.login_admin()
    assert not failing_pages(c, ADMIN)


def test_signed_in_pages_render(client):
    client.signup()
    for path in ("/account", "/account/orders", "/account/favorites"):
        assert client.get(path).status_code == 200


def test_unknown_page_is_404(client):
    assert client.get("/nope").status_code == 404


def test_canonical_urls_for_aliased_admin_routes():
    with app.test_request_context():
        from flask import url_for

        assert url_for("admin.new_listing") == "/admin/listings/new"
        assert url_for("admin.edit_listing", product_id=3) == "/admin/listings/3/edit"
        assert url_for("admin.delete_listing", product_id=3) == "/admin/listings/3/delete"
        assert url_for("shop.category", category_slug="ai") == "/category/ai"


def test_mobile_menu_is_rendered(client):
    html = client.html("/")
    assert "<details" in html and 'aria-label="Menu"' in html


def test_unexpected_error_shows_friendly_page_without_details(make_client, monkeypatch):
    from app import app
    import services.store as store

    def broken(*_a, **_kw):
        raise RuntimeError("secret internal detail")

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(store, "list_public_products", broken)
    r = make_client(locale="tr").get("/products")
    body = r.get_data(as_text=True)
    assert r.status_code == 500 and "Bir sorun oluştu" in body
    assert "secret internal detail" not in body and "Traceback" not in body
    # The page shows the request id as an error code (same id as the log line)
    code = r.headers["X-Request-ID"]
    assert re.fullmatch(r"[0-9A-F]{8}", code)
    assert "Hata kodu" in body and f">{code}</span>" in body


def test_logged_exception_carries_the_shown_error_code(client, monkeypatch, caplog):
    import services.store as store

    def broken(*_a, **_kw):
        raise RuntimeError("boom")

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(store, "list_public_products", broken)
    r = client.get("/products")
    logged = [rec for rec in caplog.records if rec.exc_info]
    assert logged and logged[-1].request_id == r.headers["X-Request-ID"]


def test_api_errors_return_the_code_as_json(client, monkeypatch):
    import views.api as api

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    monkeypatch.setattr(api, "chat_api_key", broken)
    r = client.post_json("/api/chat", {"message": "hello"})
    assert r.status_code == 500
    assert r.get_json()["code"] == r.headers["X-Request-ID"]
    assert "boom" not in r.get_data(as_text=True)


def test_admin_pages_are_fully_translated(make_client):
    """Every admin label has a Turkish text (a missing key would show up as its name)."""
    import services.store as store
    from tests.helpers import in_app

    c = make_client(locale="tr", ip="10.8.0.2")
    c.login_admin()
    # Data so the per-row labels render too: an order, a coupon, a review, a message, an email
    c.post("/cart/add/1")
    c.post("/checkout", {"full_name": "Ali", "email": "ali@x.com"})
    c.post("/admin/coupons", {"code": "TRTEST", "kind": "percent", "value": "10", "active": "1"})
    c.post("/contact", {"name": "Ali", "email": "ali@x.com", "subject": "Soru", "message": "Teslimat ne kadar sürer?"})
    order_id = in_app(store.list_orders_admin)[0]["id"]
    message_id = in_app(store.list_support_messages)[0]["id"]
    pages = ADMIN + [f"/admin/orders/{order_id}", f"/admin/messages/{message_id}", "/admin/emails/1",
                     "/admin/coupons/1/edit", "/admin/login"]
    for path in pages:
        if path.endswith(".csv"):
            continue
        html = c.html(path)
        leftovers = sorted(set(re.findall(r"\b(?:adm|admin|status|flash|pagination)_[a-z0-9_]+\b", html)))
        # Ignore identifiers that legitimately appear in markup (form field names, ids)
        leftovers = [k for k in leftovers if f'name="{k}"' not in html and f'id="{k}"' not in html]
        assert not leftovers, f"{path}: untranslated keys {leftovers}"
    assert "Yönetim paneli" in c.html("/admin/dashboard")
    assert "Siparişler" in c.html("/admin/orders")
