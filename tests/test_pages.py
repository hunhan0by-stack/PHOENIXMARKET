"""Every page renders in both languages; canonical URLs and the mobile menu."""
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
