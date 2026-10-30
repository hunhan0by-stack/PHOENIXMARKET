"""A Turkish customer's whole journey, end to end, on the throw-away test database."""
import re

import services.store as store
from tests.helpers import in_app, set_stock, stock_of
from tests.test_emails import FakeSMTP, smtp  # noqa: F401  (fixture)

INSTAGRAM = 2


def test_turkish_customer_journey_with_failing_email(make_client, admin, smtp):  # noqa: F811
    # The shop asks for the Instagram username and holds two codes for the listing
    in_app(store.update_product, INSTAGRAM, {
        "order_fields": [{"label": "Instagram kullanıcı adı", "kind": "text", "required": True}],
    })
    set_stock(INSTAGRAM, 0)
    admin.post(f"/admin/listings/{INSTAGRAM}/codes", {"codes": "KOD-AAA-111\nKOD-BBB-222"})
    assert stock_of(INSTAGRAM) == 2

    c = make_client(locale="tr", ip="10.110.0.1")
    c.signup(email="musteri@x.test", password="uzunsifre1")
    assert any("oluşturuldu" in m for m in c.flashes())
    c.post(f"/cart/add/{INSTAGRAM}")
    page = c.html("/checkout")
    assert "Sipariş bilgileri" in page and "Instagram kullanıcı adı" in page

    # A missing required answer: refused, in Turkish
    r = c.post("/checkout", {"full_name": "Ayşe Yılmaz", "email": "musteri@x.test"})
    assert r.status_code == 200 and "alanını doldurun" in r.get_data(as_text=True)

    # Email delivery is down while the order is placed: the order must still exist
    smtp.fail = True
    r = c.post("/checkout", {
        "full_name": "Ayşe Yılmaz", "email": "musteri@x.test", f"field-{INSTAGRAM}-0": "@ayse",
    })
    assert "/checkout/complete/" in r.headers["Location"]
    order = in_app(store.list_orders_admin)[0]
    assert stock_of(INSTAGRAM) == 1  # reserved at checkout
    confirmation = c.html(r.headers["Location"].replace("http://localhost", ""))
    assert f"#{order['id']}" in confirmation and "itemsatis" in confirmation
    assert any(e["status"] == "failed" for e in in_app(store.list_emails))

    # The admin confirms the payment: the code is handed out despite the failing email
    admin.post(f"/admin/orders/{order['id']}/payment", {"payment_ref": "IS-777", "notify_customer": "1"})
    order = in_app(store.get_order_by_id, order["id"])
    assert order["paid_at"] and order["fulfillment_status"] == "completed"
    track = c.html(f"/track/{order['tracking_token']}")
    assert "KOD-AAA-111" in track and "Teslimat bilgileri" in track and "@ayse" in track
    assert "KOD-BBB-222" not in track

    # The customer cannot cancel any more; a refund by the admin does not resell the code
    c.post(f"/track/{order['tracking_token']}/cancel")
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "completed"
    in_app(
        store.update_order_fulfillment, order["id"],
        fulfillment_status="cancelled", tracking_number=None, carrier_note=None,
    )
    assert stock_of(INSTAGRAM) == 1 and len(in_app(store.list_code_stock, INSTAGRAM)[None]) == 1

    # A second order receives the other code, never the first one again
    smtp.fail = False
    c.post(f"/cart/add/{INSTAGRAM}")
    c.post("/checkout", {"full_name": "Ayşe Yılmaz", "email": "musteri@x.test", f"field-{INSTAGRAM}-0": "@ayse"})
    second = in_app(store.list_orders_admin)[0]
    admin.post(f"/admin/orders/{second['id']}/payment", {"payment_ref": "IS-778"})
    [d] = in_app(store.list_deliveries, second["id"])
    assert "KOD-BBB-222" in d["body"] and "KOD-AAA-111" not in d["body"]
    assert stock_of(INSTAGRAM) == 0

    # Forgotten password: the emailed link works once, in Turkish
    c.post("/signout")
    c.post("/forgot-password", {"email": "musteri@x.test"})
    reset = smtp.sent[-1].get_body(("plain",)).get_content()
    assert "Şifre" in smtp.sent[-1]["Subject"] or "şifre" in reset.lower()
    path = re.search(r"http://localhost(/reset-password/\S+)", reset).group(1)
    c.post(path, {"password": "yenisifre123", "confirm_password": "yenisifre123"})
    assert c.get(path).headers["Location"].endswith("/forgot-password")
    c.post("/signout")
    assert c.signin(email="musteri@x.test", password="yenisifre123").status_code == 302
    history = c.html("/account/orders")
    assert f"#{order['id']}" in history and f"#{second['id']}" in history


def test_order_total_cannot_be_set_by_the_customer(client):
    """Prices, discounts and totals come from the database; extra form fields are ignored."""
    product = in_app(store.get_product, 1)
    client.post("/cart/add/1", {"qty": "2", "price": "0.01", "unit_price_usd": "0.01"})
    client.post("/checkout", {
        "full_name": "Buyer", "email": "buyer@x.com",
        "total": "0.01", "total_price": "0.01", "discount_usd": "999", "coupon": "FREE", "price": "0",
    })
    order = in_app(store.get_order_by_id, in_app(store.list_orders_admin)[0]["id"])
    assert order["total_price"] == round(product["base_price"] * 2, 2)
    assert order["discount_usd"] == 0 and order["coupon_code"] is None
    assert order["line_items"][0]["price"] == product["base_price"]


def test_cart_session_cannot_carry_prices(client):
    """The session cart holds only keys and quantities; a forged price inside it changes nothing."""
    product = in_app(store.get_product, 1)
    with client.c.session_transaction() as s:
        s["cart"] = {"1": 1, "1:price": 0.01, "2:999": 1}
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    order = in_app(store.get_order_by_id, in_app(store.list_orders_admin)[0]["id"])
    assert [li["price"] for li in order["line_items"]] == [product["base_price"]]
