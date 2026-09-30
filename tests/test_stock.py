"""Stock limits in the cart, reservation at checkout, and release on cancel."""
import pytest

import services.store as store
from tests.helpers import in_app, set_stock, stock_of


def checkout(client, email="a@x.com", name="A"):
    return client.post("/checkout", {"full_name": name, "email": email})


def test_cart_quantity_is_limited_to_stock(make_client):
    set_stock(1, 2)
    c = make_client(locale="tr")
    for _ in range(3):
        c.post("/cart/add/1")
    assert c.session()["cart"]["1"] == 2
    assert any("yalnızca 2" in m for m in c.flashes())
    c.post("/cart/update/1", {"qty": "9"})
    assert c.session()["cart"]["1"] == 2


def test_checkout_reserves_stock_and_blocks_second_buyer(make_client):
    set_stock(1, 2)
    first, second = make_client(), make_client(ip="10.0.0.2")
    for c in (first, second):
        c.post("/cart/add/1")
        c.post("/cart/add/1")
    r = checkout(first)
    assert r.status_code == 302 and "/checkout/complete/" in r.headers["Location"]
    assert stock_of(1) == 0
    r = checkout(second, email="b@x.com")
    assert r.headers["Location"].endswith("/cart")
    assert in_app(store.count_orders) == 1


def test_out_of_stock_listing_cannot_be_added(client):
    set_stock(1, 0)
    html = client.html("/products")
    assert "Out of stock" in html and "disabled" in html
    client.post("/cart/add/1")
    assert "1" not in (client.session().get("cart") or {})


def test_inactive_listing_cannot_be_added(client):
    in_app(store.update_product, 2, {"status": "inactive"})
    client.post("/cart/add/2")
    assert "2" not in (client.session().get("cart") or {})


def test_low_stock_badge_and_dashboard(client, admin):
    set_stock(3, 2)
    assert "Only 2 left" in client.html("/products")
    html = admin.html("/admin/dashboard")
    assert "Low stock" in html and "TikTok Views" in html


def test_cancel_and_reopen_move_stock(client, admin):
    set_stock(1, 2)
    client.post("/cart/add/1")
    client.post("/cart/add/1")
    checkout(client)
    order_id = in_app(store.list_orders_admin)[0]["id"]
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "cancelled"})
    assert stock_of(1) == 2
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "processing"})
    assert stock_of(1) == 0
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "cancelled"})
    set_stock(1, 0)
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "pending"})
    assert in_app(store.get_order_by_id, order_id)["fulfillment_status"] == "cancelled"


def test_create_order_rolls_back_partial_reservation():
    set_stock(2, 5)
    set_stock(3, 1)
    with pytest.raises(store.OutOfStockError) as excinfo:
        in_app(
            store.create_order,
            tracking_token="race-1", user_email="r@x.com", full_name="R", notes="",
            total_price=1, total_currency="USD",
            line_items=[
                {"product_id": 2, "product_name": "P2", "quantity": 2, "unit_price_usd": 1},
                {"product_id": 3, "product_name": "P3", "quantity": 3, "unit_price_usd": 1},
            ],
        )
    assert excinfo.value.product_name == "P3"
    assert stock_of(2) == 5 and stock_of(3) == 1
    assert in_app(store.count_orders) == 0


def test_pre_v2_orders_never_touch_stock(admin):
    """Orders created before stock tracking have stock_reserved = 0."""
    def insert_legacy_order():
        db = store.get_db()
        db.execute(
            "INSERT INTO orders (tracking_token, user_email, full_name, total_price, created_at, updated_at) "
            "VALUES ('legacy-1', 'l@x.com', 'L', 1, '2025-01-01', '2025-01-01')"
        )
        order_id = db.execute("SELECT id FROM orders WHERE tracking_token = 'legacy-1'").fetchone()["id"]
        db.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price_usd) "
            "VALUES (?, 4, 'ChatGPT Accounts', 3, 1)",
            (order_id,),
        )
        db.commit()
        return order_id

    order_id = in_app(insert_legacy_order)
    before = stock_of(4)
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "cancelled"})
    assert stock_of(4) == before
    assert in_app(store.get_order_by_id, order_id)["fulfillment_status"] == "cancelled"
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "pending"})
    assert stock_of(4) == before
