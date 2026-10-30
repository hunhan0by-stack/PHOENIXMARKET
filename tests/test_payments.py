"""Payments: admin confirmation, its interplay with expiry, and itemsatis links per listing/package."""
from datetime import datetime, timedelta, timezone

import services.store as store
import services.unpaid_orders as unpaid_orders
from app import app
from tests.helpers import in_app

INSTAGRAM, CANVA = 2, 1
SITE_LINK = "https://www.itemsatis.com/magaza/phoenixmarket-test"
LISTING_LINK = "https://www.itemsatis.com/ilan/instagram-takipci"
PACKAGE_LINK = "https://www.itemsatis.com/ilan/instagram-1k"


def outbox(kind):
    return [e for e in in_app(store.list_emails, 100) if e["kind"] == kind]


def place_order(client, product_id=INSTAGRAM, email="buyer@x.com", **cart):
    client.post(f"/cart/add/{product_id}", cart or None)
    client.post("/checkout", {"full_name": "Buyer", "email": email})
    return in_app(store.list_orders_admin)[0]["id"]


def order(order_id):
    return in_app(store.get_order_by_id, order_id)


def events(order_id, customer_view=False):
    return [e["event_type"] for e in in_app(store.list_order_events, order_id, customer_view=customer_view)]


# --- admin confirms the payment -------------------------------------------------
def test_confirming_payment_moves_order_to_processing(client, admin):
    order_id = place_order(client)
    r = admin.post(f"/admin/orders/{order_id}/payment", {"payment_ref": " IS-12345 ", "notify_customer": "1"})
    assert r.status_code == 302
    o = order(order_id)
    assert o["paid_at"] is not None and o["payment_ref"] == "IS-12345"
    assert o["fulfillment_status"] == "processing"
    assert events(order_id, customer_view=True) == ["payment_received", "status_changed"]
    [email] = outbox("order_update")
    assert "We have received your payment" in email["body_text"]
    html = admin.html(f"/admin/orders/{order_id}")
    assert "Payment received" in html and "IS-12345" in html


def test_double_click_records_the_payment_once(client, admin):
    order_id = place_order(client)
    admin.post(f"/admin/orders/{order_id}/payment", {})
    admin.clear_flashes()
    admin.post(f"/admin/orders/{order_id}/payment", {})
    assert any("changed" in m for m in admin.flashes())
    assert events(order_id).count("payment_received") == 1


def test_paid_order_is_never_cancelled_automatically(client, admin):
    order_id = place_order(client)
    admin.post(f"/admin/orders/{order_id}/payment", {})

    def force_pending_overdue():
        db = store.get_db()
        db.execute(
            "UPDATE orders SET fulfillment_status = 'pending', payment_due_at = ? WHERE id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), order_id),
        )
        db.commit()

    in_app(force_pending_overdue)
    with app.test_request_context():
        assert unpaid_orders.cancel_overdue_orders() == []
    assert order(order_id)["fulfillment_status"] == "pending"


def test_expired_order_needs_reopening_before_payment(client, admin):
    order_id = place_order(client)
    with app.test_request_context():
        unpaid_orders.cancel_overdue_orders(datetime.now(timezone.utc) + timedelta(hours=25))
    admin.post(f"/admin/orders/{order_id}/payment", {})
    assert any("set it back to Pending" in m for m in admin.flashes())
    assert order(order_id)["paid_at"] is None
    assert "set its status back to Pending" in admin.html(f"/admin/orders/{order_id}")


def test_undo_payment_is_internal(client, admin):
    order_id = place_order(client)
    admin.post(f"/admin/orders/{order_id}/payment", {"payment_ref": "X1"})
    admin.post(f"/admin/orders/{order_id}/payment/undo")
    o = order(order_id)
    assert o["paid_at"] is None and o["payment_ref"] is None
    assert "payment_undone" in events(order_id)
    assert "payment_undone" not in events(order_id, customer_view=True)


def test_orders_list_search_and_csv_show_payments(client, admin):
    order_id = place_order(client)
    admin.post(f"/admin/orders/{order_id}/payment", {"payment_ref": "ITEM-777"})
    assert "✓ paid" in admin.html("/admin/orders")
    assert f"#{order_id}" in admin.html("/admin/orders?q=item-777")
    csv_text = admin.get("/admin/orders.csv").get_data(as_text=True)
    assert "paid_at,payment_ref" in csv_text.splitlines()[0] and "ITEM-777" in csv_text


def test_payment_routes_are_admin_only(client):
    order_id = place_order(client)
    for path in (f"/admin/orders/{order_id}/payment", f"/admin/orders/{order_id}/payment/undo"):
        assert client.post(path, {}).headers["Location"].endswith("/admin/login")
    assert order(order_id)["paid_at"] is None


# --- where the customer pays ----------------------------------------------------
def set_listing_link(admin, product_id, url):
    product = in_app(store.get_product, product_id)
    return admin.post(f"/admin/listings/{product_id}/edit", {
        "name": product["name"], "description": product["description"],
        "base_price": str(product["base_price"]), "base_currency": "USD",
        "listing_language": "en", "category": product["category"],
        "stock": str(product["stock"]), "status": "active", "payment_url": url,
    })


def test_confirmation_uses_the_site_wide_link_by_default(client):
    order_id = place_order(client)
    html = client.html(f"/checkout/complete/{order(order_id)['tracking_token']}")
    assert f'href="{SITE_LINK}"' in html
    assert f'data-copy="#{order_id}"' in html


def test_listing_link_is_used_for_its_orders(client, admin):
    set_listing_link(admin, INSTAGRAM, LISTING_LINK)
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] == LISTING_LINK
    order_id = place_order(client)
    o = order(order_id)
    assert f'href="{LISTING_LINK}"' in client.html(f"/checkout/complete/{o['tracking_token']}")
    assert f'href="{LISTING_LINK}"' in client.html(f"/track/{o['tracking_token']}")
    [email] = outbox("order_confirmation")
    assert LISTING_LINK in email["body_text"] and SITE_LINK not in email["body_text"]


def test_package_link_beats_listing_link_and_mixed_carts_get_one_button_each(client, admin):
    set_listing_link(admin, INSTAGRAM, LISTING_LINK)
    admin.post(f"/admin/listings/{INSTAGRAM}/packages", {
        "name": "1K", "price": "4.99", "stock": "10", "sort_order": "0", "active": "1",
        "payment_url": PACKAGE_LINK,
    })
    one_k = next(v["id"] for v in in_app(store.list_variants, INSTAGRAM) if v["name"] == "1K")
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    client.post(f"/cart/add/{CANVA}")
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    o = order(in_app(store.list_orders_admin)[0]["id"])
    html = client.html(f"/checkout/complete/{o['tracking_token']}")
    assert f'href="{PACKAGE_LINK}"' in html and f'href="{SITE_LINK}"' in html
    assert f'href="{LISTING_LINK}"' not in html
    assert "Instagram Followers – 1K" in html and "paid on separate itemsatis listings" in html


def test_invalid_links_are_rejected(admin):
    r = set_listing_link(admin, INSTAGRAM, "javascript:alert(1)")
    assert "must start with https://www.itemsatis.com/" in r.get_data(as_text=True)
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] is None
    admin.post(f"/admin/listings/{INSTAGRAM}/packages", {
        "name": "2K", "price": "4.99", "stock": "1", "sort_order": "0", "active": "1",
        "payment_url": "ftp://example.com/x",
    })
    assert in_app(store.list_variants, INSTAGRAM) == []


def test_clearing_the_link_falls_back_to_the_site_wide_one(admin):
    set_listing_link(admin, INSTAGRAM, LISTING_LINK)
    set_listing_link(admin, INSTAGRAM, "")
    assert in_app(store.get_product, INSTAGRAM)["payment_url"] is None


def test_track_page_offers_payment_only_while_awaiting_it(client, admin):
    order_id = place_order(client)
    token = order(order_id)["tracking_token"]
    assert f'href="{SITE_LINK}"' in client.html(f"/track/{token}")
    admin.post(f"/admin/orders/{order_id}/payment", {})
    html = client.html(f"/track/{token}")
    assert f'href="{SITE_LINK}"' not in html
    assert "Payment received" in html
