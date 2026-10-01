"""Checkout safeguards: shown-price confirmation, input limits, coupon use on cancel."""
import re

import services.store as store
from tests.helpers import in_app


def quote_on_page(client):
    html = client.html("/checkout")
    return re.search(r'name="quote" value="([0-9a-f]+)"', html).group(1)


def place(client, **extra):
    return client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com", **extra})


def single_use_coupon(code="ONCE"):
    return in_app(store.create_coupon, {
        "code": code, "kind": "fixed", "value": 2, "min_order_usd": 0, "max_uses": 1,
        "starts_at": None, "expires_at": None, "active": True,
    })


def test_order_is_placed_at_the_prices_shown(client):
    client.post("/cart/add/1")
    r = place(client, quote=quote_on_page(client))
    assert "/checkout/complete/" in r.headers["Location"]


def test_price_change_after_page_load_requires_confirmation(client):
    client.post("/cart/add/1")
    quote = quote_on_page(client)
    in_app(store.update_product, 1, {"base_price": 11.49})
    r = place(client, quote=quote)
    assert r.status_code == 200 and in_app(store.count_orders) == 0
    body = r.get_data(as_text=True)
    assert "Prices or your discount changed" in body and "11.49" in body
    # Confirming the updated page goes through, at the new price
    r = place(client, quote=quote_on_page(client))
    assert "/checkout/complete/" in r.headers["Location"]
    assert in_app(store.list_orders_admin)[0]["total_price"] == 11.49


def test_checkout_field_lengths_are_limited(client):
    client.post("/cart/add/1")
    r = place(client, notes="x" * 2001)
    assert r.status_code == 200 and in_app(store.count_orders) == 0
    assert "shorten" in r.get_data(as_text=True)


def test_cancelled_order_gives_single_use_coupon_back(make_client, admin):
    single_use_coupon()
    first = make_client()
    first.post("/cart/add/1")
    first.post("/cart/coupon", {"code": "once"})
    place(first)
    order = in_app(store.list_orders_admin)[0]
    assert in_app(store.get_coupon_by_code, "ONCE")["used_count"] == 1
    first.post(f"/track/{order['tracking_token']}/cancel")
    assert in_app(store.get_coupon_by_code, "ONCE")["used_count"] == 0
    # Re-opening the order takes the use again
    admin.post(f"/admin/orders/{order['id']}", {"fulfillment_status": "pending"})
    assert in_app(store.get_coupon_by_code, "ONCE")["used_count"] == 1
