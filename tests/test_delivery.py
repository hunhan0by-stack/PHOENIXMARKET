"""Delivery time on listings and the trust links on the product page."""
import pytest

import services.store as store
from services.web import delivery_label
from tests.helpers import in_app

CANVA = 1


def save_listing(admin, **over):
    product = in_app(store.get_product, CANVA)
    data = {
        "name": product["name"], "description": product["description"],
        "base_price": str(product["base_price"]), "base_currency": "USD", "listing_language": "en",
        "category": product["category"], "stock": str(product["stock"]), "status": "active",
    }
    data.update(over)
    return admin.post(f"/admin/listings/{CANVA}/edit", data)


def product_page(client):
    return client.html(f"/product/{in_app(store.get_product, CANVA)['slug']}")


@pytest.mark.parametrize("hours, en, tr", [
    (1, "Delivered within 1 hour", "1 saat içinde teslim"),
    (47, "Delivered within 47 hours", "47 saat içinde teslim"),
    (48, "Delivered within 2 days", "2 gün içinde teslim"),
    (50, "Delivered within 50 hours", "50 saat içinde teslim"),
    (720, "Delivered within 30 days", "30 gün içinde teslim"),
    (None, "", ""),
])
def test_delivery_label(hours, en, tr):
    assert delivery_label(hours, "en") == en and delivery_label(hours, "tr") == tr


def test_delivery_time_shows_on_the_product_page(admin, make_client):
    save_listing(admin, delivery_hours="24")
    assert in_app(store.get_product, CANVA)["delivery_hours"] == 24
    assert "Delivered within 24 hours" in product_page(make_client())
    assert "24 saat içinde teslim" in product_page(make_client(locale="tr", ip="10.0.0.3"))


@pytest.mark.parametrize("value", ["0", "721", "abc", "-5", "2.5"])
def test_invalid_delivery_times_are_rejected(admin, value):
    save_listing(admin, delivery_hours="24")
    r = save_listing(admin, delivery_hours=value)
    assert "between 1 and 720" in r.get_data(as_text=True)
    assert in_app(store.get_product, CANVA)["delivery_hours"] == 24


def test_clearing_removes_the_promise_but_keeps_trust_links(admin, client):
    save_listing(admin, delivery_hours="24")
    save_listing(admin, delivery_hours="")
    assert in_app(store.get_product, CANVA)["delivery_hours"] is None
    html = product_page(client)
    assert "Delivered within" not in html
    assert 'href="/legal/returns"' in html and "Contact support" in html
