"""Discount codes: applying in the cart, checkout accounting and admin management."""
from datetime import datetime, timedelta, timezone

import pytest

import services.store as store
from tests.helpers import in_app, stock_of

NOW = datetime.now(timezone.utc)


def make_coupon(code="WELCOME10", kind="percent", value=10, **extra):
    data = {"code": code, "kind": kind, "value": value, "min_order_usd": 0, "max_uses": None,
            "starts_at": None, "expires_at": None, "active": True}
    data.update(extra)
    return in_app(store.create_coupon, data)


def cart_with_instagram(client):
    client.post("/cart/add/2")  # Instagram Followers, $14.99


def checkout(client, email="c@x.com"):
    return client.post("/checkout", {"full_name": "C", "email": email})


def test_percent_coupon_through_checkout(client):
    make_coupon()
    cart_with_instagram(client)
    client.post("/cart/coupon", {"code": "welcome10"})  # case-insensitive
    assert any("applied" in m for m in client.flashes())
    html = client.html("/cart")
    assert "Discount (WELCOME10)" in html and "13.49 $" in html
    assert "13.49 $" in client.html("/checkout")

    assert checkout(client).status_code == 302
    order = in_app(store.list_orders_admin)[0]
    full = in_app(store.get_order_by_id, order["id"])
    assert full["discount_usd"] == 1.5 and full["coupon_code"] == "WELCOME10"
    assert full["total_price"] == pytest.approx(13.49)
    assert in_app(store.get_coupon_by_code, "WELCOME10")["used_count"] == 1
    assert "coupon" not in client.session()
    assert "Discount (WELCOME10)" in client.html(f"/track/{full['tracking_token']}")


def test_fixed_coupon_never_exceeds_subtotal(client):
    make_coupon(code="BIG", kind="fixed", value=500)
    cart_with_instagram(client)
    client.post("/cart/coupon", {"code": "BIG"})
    checkout(client)
    order = in_app(store.list_orders_admin)[0]
    assert order["total_price"] == 0 and order["discount_usd"] == pytest.approx(14.99)


@pytest.mark.parametrize(
    "extra,message",
    [
        ({"active": False}, "not valid"),
        ({"expires_at": NOW - timedelta(days=1)}, "expired"),
        ({"starts_at": NOW + timedelta(days=1)}, "not active yet"),
        ({"max_uses": 1, "used": 1}, "usage limit"),
        ({"min_order_usd": 50}, "at least 50.00 $"),
    ],
)
def test_coupon_rejections(client, extra, message):
    used = extra.pop("used", 0)
    coupon_id = make_coupon(code="TEST", **extra)
    if used:
        in_app(lambda: (store.get_db().execute("UPDATE coupons SET used_count = ? WHERE id = ?", (used, coupon_id)), store.get_db().commit()))
    cart_with_instagram(client)
    client.post("/cart/coupon", {"code": "TEST"})
    assert any(message in m for m in client.flashes())
    assert "coupon" not in client.session()


def test_unknown_code(client):
    cart_with_instagram(client)
    client.post("/cart/coupon", {"code": "NOPE"})
    assert any("not valid" in m for m in client.flashes())


def test_coupon_dropped_when_cart_falls_below_minimum(client):
    make_coupon(code="MIN20", min_order_usd=20)
    client.post("/cart/add/2")
    client.post("/cart/add/2")  # 29.98
    client.post("/cart/coupon", {"code": "MIN20"})
    assert client.session()["coupon"] == "MIN20"
    client.post("/cart/update/2", {"qty": "1"})  # 14.99 < 20
    client.clear_flashes()
    html = client.html("/cart")
    assert "Discount (MIN20)" not in html and "coupon" not in client.session()


def test_last_use_race_rolls_back_the_order(make_client):
    make_coupon(code="ONCE", max_uses=1)
    a, b = make_client(ip="10.30.0.1"), make_client(ip="10.30.0.2")
    for c in (a, b):
        cart_with_instagram(c)
        c.post("/cart/coupon", {"code": "ONCE"})
    stock_before = stock_of(2)
    assert checkout(a).status_code == 302
    # b applied the code while it was still available; checkout must refuse it now
    r = checkout(b, email="b@x.com")
    assert r.headers["Location"].endswith("/cart")
    assert in_app(store.count_orders) == 1 and stock_of(2) == stock_before - 1


def test_create_order_consumes_coupon_atomically():
    coupon_id = make_coupon(code="ATOMIC", max_uses=1)
    coupon = in_app(store.get_coupon, coupon_id)

    def place(token):
        return in_app(
            store.create_order,
            tracking_token=token, user_email="a@x.com", full_name="A", notes="",
            total_price=13.49, total_currency="USD",
            line_items=[{"product_id": 2, "product_name": "Instagram Followers", "quantity": 1, "unit_price_usd": 14.99}],
            coupon=coupon, discount_usd=1.5,
        )

    stock_before = stock_of(2)
    place("atomic-1")
    with pytest.raises(store.CouponUnavailableError):
        place("atomic-2")  # stale coupon dict: the UPDATE guard must refuse it
    assert in_app(store.count_orders) == 1
    assert stock_of(2) == stock_before - 1
    assert in_app(store.get_coupon, coupon_id)["used_count"] == 1


def test_coupon_guessing_is_rate_limited(client):
    cart_with_instagram(client)
    for i in range(20):
        client.post("/cart/coupon", {"code": f"GUESS{i}"})
    client.clear_flashes()
    make_coupon(code="REAL")
    client.post("/cart/coupon", {"code": "REAL"})
    assert any("Too many attempts" in m for m in client.flashes())


def test_admin_coupon_management(admin):
    base = {"code": "spring-25", "kind": "percent", "value": "25", "min_order_usd": "0", "max_uses": "", "active": "1"}
    admin.post("/admin/coupons", base)
    coupon = in_app(store.get_coupon_by_code, "SPRING-25")
    assert coupon and coupon["value"] == 25 and coupon["active"]

    # Invalid submissions re-render the form, so the message is in the response.
    for bad, message in [
        ({"code": "x"}, "3–32"),
        ({"value": "150"}, "percent 0–100"),
        ({"code": "OTHER", "starts_at": "2026-05-10", "expires_at": "2026-05-01"}, "end must be after"),
        ({}, "already exists"),
    ]:
        r = admin.post("/admin/coupons", {**base, **bad})
        assert r.status_code == 200 and message in r.get_data(as_text=True)
    assert in_app(store.get_coupon_by_code, "OTHER") is None

    admin.post(f"/admin/coupons/{coupon['id']}/edit", {**base, "value": "30", "max_uses": "5", "expires_at": "2099-01-01"})
    edited = in_app(store.get_coupon, coupon["id"])
    assert edited["value"] == 30 and edited["max_uses"] == 5 and edited["expires_at"].year == 2099
    admin.post(f"/admin/coupons/{coupon['id']}/toggle")
    assert not in_app(store.get_coupon, coupon["id"])["active"]
    html = admin.html("/admin/coupons")
    assert "SPRING-25" in html and "Inactive" in html
    admin.post(f"/admin/coupons/{coupon['id']}/delete")
    assert in_app(store.get_coupon, coupon["id"]) is None


def test_csv_includes_discount(client, admin):
    make_coupon()
    cart_with_instagram(client)
    client.post("/cart/coupon", {"code": "WELCOME10"})
    checkout(client)
    text = admin.get("/admin/orders.csv").get_data(as_text=True)
    assert "discount_usd,coupon" in text and "1.50,WELCOME10" in text
