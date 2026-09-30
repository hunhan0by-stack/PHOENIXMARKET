"""Favourites, customer self-cancellation and back-in-stock alerts."""
import services.store as store
from tests.helpers import in_app, set_stock, stock_of


def outbox(kind):
    return [e for e in in_app(store.list_emails, 100) if e["kind"] == kind]


def listing_form(**over):
    data = {"name": "TikTok Views", "description": "Increase your TikTok views", "base_price": "12.99",
            "base_currency": "USD", "listing_language": "en", "category": "Social Media",
            "stock": "5", "status": "active"}
    data.update(over)
    return data


# --- favourites -----------------------------------------------------------------
def test_guest_is_asked_to_sign_in(client):
    r = client.post("/wishlist/2")
    assert r.headers["Location"].endswith("/signin")


def test_toggle_favorite_and_favorites_page(client):
    client.signup()
    client.post("/wishlist/2")
    assert in_app(store.wishlist_product_ids, client.user_id()) == {2}
    html = client.html("/products")
    assert 'aria-pressed="true"' in html and html.count('aria-pressed="true"') == 1
    favorites = client.html("/account/favorites")
    assert "Instagram Followers" in favorites and "TikTok Views" not in favorites
    assert "Favorites (1)" in client.html("/account")
    client.post("/wishlist/2")
    assert in_app(store.wishlist_product_ids, client.user_id()) == set()
    assert "saved any services yet" in client.html("/account/favorites")


def test_inactive_listing_cannot_be_favorited(client):
    client.signup()
    in_app(store.update_product, 3, {"status": "inactive"})
    assert client.post("/wishlist/3").status_code == 404


# --- customer cancellation --------------------------------------------------------
def place_order(client, product_id=1):
    client.post(f"/cart/add/{product_id}")
    client.post("/checkout", {"full_name": "C", "email": "c@x.com"})
    return in_app(store.list_orders_admin)[0]


def test_customer_cancels_pending_order(client):
    stock_before = stock_of(1)
    order = place_order(client)
    token = order["tracking_token"]
    assert "Cancel order" in client.html(f"/track/{token}")
    client.post(f"/track/{token}/cancel")
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "cancelled"
    assert stock_of(1) == stock_before
    events = in_app(store.list_order_events, order["id"])
    assert [e["event_type"] for e in events] == ["cancelled_by_customer"]
    html = client.html(f"/track/{token}")
    assert "Cancelled by the customer" in html and "Cancel order" not in html
    assert "Status changed to Cancelled" in outbox("order_update")[0]["body_text"]


def test_processing_order_cannot_be_cancelled_by_customer(client, admin):
    order = place_order(client)
    admin.post(f"/admin/orders/{order['id']}", {"fulfillment_status": "processing"})
    client.post(f"/track/{order['tracking_token']}/cancel")
    assert any("can no longer be cancelled" in m for m in client.flashes())
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "processing"
    assert client.post("/track/not-a-token/cancel").status_code == 404


# --- back-in-stock alerts -----------------------------------------------------------
def test_alert_form_only_for_out_of_stock(client):
    assert "Notify me" not in client.html("/product/tiktok-views")
    set_stock(3, 0)
    assert "Notify me" in client.html("/product/tiktok-views")
    client.post("/product/3/notify", {"email": "not-an-email"})
    assert any("valid email" in m for m in client.flashes())
    client.post("/product/3/notify", {"email": "Wait@X.com"})
    client.post("/product/3/notify", {"email": "wait@x.com"})
    assert len(in_app(store.pending_stock_alerts, 3)) == 1


def test_restock_by_admin_sends_alerts_once(make_client, admin):
    set_stock(3, 0)
    tr = make_client(locale="tr", ip="10.70.0.1")
    tr.post("/product/3/notify", {"email": "wait@x.com"})
    assert "🔔 1" in admin.html("/admin/listings")
    admin.post("/admin/listings/3/edit", listing_form(stock="5"))
    [email] = outbox("back_in_stock")
    assert email["to_address"] == "wait@x.com" and email["subject"] == "TikTok Views yeniden stokta"
    assert "http://localhost/product/tiktok-views" in email["body_text"]
    assert in_app(store.pending_stock_alerts, 3) == []
    admin.post("/admin/listings/3/edit", listing_form(stock="9"))
    assert len(outbox("back_in_stock")) == 1


def test_cancellations_that_restore_stock_send_alerts(make_client, admin):
    set_stock(1, 1)
    buyer = make_client(ip="10.71.0.1")
    order = place_order(buyer)
    assert stock_of(1) == 0
    make_client(ip="10.71.0.2").post("/product/1/notify", {"email": "first@x.com"})
    admin.post(f"/admin/orders/{order['id']}", {"fulfillment_status": "cancelled"})
    assert [e["to_address"] for e in outbox("back_in_stock")] == ["first@x.com"]

    second = place_order(make_client(ip="10.71.0.3"))
    make_client(ip="10.71.0.4").post("/product/1/notify", {"email": "second@x.com"})
    buyer.post(f"/track/{second['tracking_token']}/cancel")
    assert sorted(e["to_address"] for e in outbox("back_in_stock")) == ["first@x.com", "second@x.com"]
