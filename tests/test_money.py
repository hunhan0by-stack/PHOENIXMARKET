"""Money: half-up cents, discounts, conversions and order totals that add up."""
import random

import pytest

import services.coupons as coupons
import services.currency as currency
import services.store as store
from services.money import money
from tests.helpers import in_app


@pytest.mark.parametrize("value, expected", [
    (2.675, 2.68),      # round() gives 2.67 (binary float + banker's rounding)
    (0.125, 0.13),      # round() gives 0.12
    (1.005, 1.01),      # round() gives 1.0
    (344.655, 344.66),
    (2.2485, 2.25),
    (19.99 * 999, 19970.01),
    (0.1 + 0.2, 0.3),
    (5, 5.0),
    (None, 0.0),
])
def test_money_rounds_half_up_to_cents(value, expected):
    assert money(value) == expected


@pytest.mark.parametrize("kind, value, subtotal, expected", [
    ("percent", 15, 14.99, 2.25),       # 2.2485
    ("percent", 10, 0.25, 0.03),        # 0.025
    ("percent", 150, 20.00, 20.00),     # never more than 100 %
    ("fixed", 5, 3.50, 3.50),           # never more than the subtotal
    ("fixed", 2.5, 10.00, 2.50),
    ("percent", 0, 10.00, 0.0),
])
def test_discount_is_capped_and_rounded(kind, value, subtotal, expected):
    assert coupons.discount_for({"kind": kind, "value": value}, subtotal) == expected


def test_currency_display_uses_half_up(monkeypatch):
    monkeypatch.setattr(currency, "get_rates", lambda: {"USD": 1.0, "TRY": 34.5, "EUR": 0.92})
    assert currency.convert(9.99, "USD", "TRY") == 344.66   # 344.655
    assert currency.convert(344.66, "TRY", "USD") == 9.99
    assert currency.convert(0, "USD", "TRY") == 0.0


def make_listing(name, price, stock=100):
    data = {"name": name, "slug": name.lower().replace(" ", "-"), "description": "x", "base_price": price,
            "base_currency": "USD", "listing_language": "en", "category": "Test", "stock": stock,
            "status": "active", "owner": "admin", "image_path": ""}
    return in_app(store.create_product, data)


def test_order_total_is_exactly_the_sum_of_its_lines(client):
    """Prices like 0.10 + 0.20 + 0.70 must store 1.00, not 0.9999999."""
    for name, price in (("Dime", 0.10), ("Two Dimes", 0.20), ("Seven Dimes", 0.70)):
        client.post(f"/cart/add/{make_listing(name, price)}")
    client.post("/checkout", {"full_name": "A", "email": "a@x.com"})
    order = in_app(store.get_order_by_id, in_app(store.list_orders_admin)[0]["id"])
    assert order["total_price"] == 1.0
    assert repr(order["total_price"]) == "1.0"


def test_random_carts_with_discounts_always_add_up(make_client):
    """Invariant: stored total == sum(qty × unit) − discount, to the cent, never negative."""
    rng = random.Random(20261022)
    listings = [make_listing(f"Item {i}", round(rng.uniform(0.01, 99.99), 2)) for i in range(6)]
    in_app(store.create_coupon, {"code": "PCT17", "kind": "percent", "value": 17, "min_order_usd": 0,
                                 "max_uses": None, "starts_at": None, "expires_at": None, "active": True})
    for n in range(8):
        c = make_client(ip=f"10.77.0.{n}")
        for product_id in rng.sample(listings, rng.randint(1, 4)):
            for _ in range(rng.randint(1, 3)):
                c.post(f"/cart/add/{product_id}")
        if n % 2:
            c.post("/cart/coupon", {"code": "PCT17"})
        c.post("/checkout", {"full_name": "R", "email": f"r{n}@x.com"})
    orders = in_app(store.list_orders_admin)
    assert len(orders) == 8
    for row in orders:
        order = in_app(store.get_order_by_id, row["id"])
        lines = sum(money(li["quantity"] * li["price"]) for li in order["line_items"])
        assert order["total_price"] == money(lines - order["discount_usd"])
        assert order["total_price"] >= 0
        if order["coupon_code"]:
            assert order["discount_usd"] == money(lines * 0.17)
