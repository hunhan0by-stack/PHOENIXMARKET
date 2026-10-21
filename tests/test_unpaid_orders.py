"""Unpaid orders: payment deadline at checkout, automatic cancellation, emails."""
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
from app import app
import services.store as store
import services.unpaid_orders as unpaid_orders
from tests.helpers import in_app, set_stock, stock_of

LATER = timedelta(hours=25)


def place_order(client, email="buyer@x.com", product_id=2):
    client.post(f"/cart/add/{product_id}")
    r = client.post("/checkout", {"full_name": "Buyer", "email": email})
    assert "/checkout/complete/" in r.headers["Location"]
    return in_app(store.list_orders_admin)[0]["id"]


def order(order_id):
    return in_app(store.get_order_by_id, order_id)


def run_expiry(now):
    with app.test_request_context():  # emails build absolute links
        return unpaid_orders.cancel_overdue_orders(now)


def outbox(kind):
    return [e for e in in_app(store.list_emails, 100) if e["kind"] == kind]


@pytest.fixture
def window():
    previous = app.config["PENDING_ORDER_TTL_HOURS"]
    yield lambda hours: app.config.__setitem__("PENDING_ORDER_TTL_HOURS", hours)
    app.config["PENDING_ORDER_TTL_HOURS"] = previous


def test_checkout_sets_a_payment_deadline(client):
    before = datetime.now(timezone.utc)
    o = order(place_order(client))
    assert app.config["PENDING_ORDER_TTL_HOURS"] == 24
    assert before + timedelta(hours=24) <= o["payment_due_at"] <= datetime.now(timezone.utc) + timedelta(hours=24)


def test_no_deadline_when_disabled(client, window):
    window(0)
    assert order(place_order(client))["payment_due_at"] is None


def test_overdue_order_is_cancelled_and_stock_released(client):
    set_stock(2, 3)
    order_id = place_order(client)
    assert stock_of(2) == 2

    assert run_expiry(datetime.now(timezone.utc)) == []  # not due yet
    assert run_expiry(datetime.now(timezone.utc) + LATER) == [order_id]

    o = order(order_id)
    assert o["fulfillment_status"] == "cancelled"
    assert stock_of(2) == 3
    events = in_app(store.list_order_events, order_id, customer_view=True)
    assert [e["event_type"] for e in events] == ["expired"]
    # Running again changes nothing (stock returned exactly once)
    assert run_expiry(datetime.now(timezone.utc) + LATER) == []
    assert stock_of(2) == 3


def test_expiry_returns_the_coupon_use(client):
    in_app(store.create_coupon, {
        "code": "ONCE", "kind": "percent", "value": 10, "min_order_usd": 0, "max_uses": 1,
        "starts_at": None, "expires_at": None, "active": True,
    })
    client.post("/cart/add/2")
    client.post("/cart/coupon", {"code": "ONCE"})
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    assert in_app(store.get_coupon_by_code, "ONCE")["used_count"] == 1
    run_expiry(datetime.now(timezone.utc) + LATER)
    assert in_app(store.get_coupon_by_code, "ONCE")["used_count"] == 0


def test_customer_is_emailed_in_their_language(make_client):
    c = make_client(locale="tr")
    order_id = place_order(c)
    run_expiry(datetime.now(timezone.utc) + LATER)
    [email] = outbox("order_update")
    assert email["to_address"] == "buyer@x.com"
    assert "otomatik olarak iptal edildi" in email["body_text"]
    assert f"#{order_id}" in email["subject"]


def test_paid_or_handled_orders_are_left_alone(client, admin):
    paid = place_order(client, "paid@x.com")
    admin.post(f"/admin/orders/{paid}", {"fulfillment_status": "processing"})
    customer_cancelled = place_order(client, "c@x.com", product_id=3)
    in_app(
        store.update_order_fulfillment, customer_cancelled, fulfillment_status="cancelled",
        tracking_number=None, carrier_note=None, actor="customer",
    )
    assert run_expiry(datetime.now(timezone.utc) + LATER) == []
    assert order(paid)["fulfillment_status"] == "processing"


def test_orders_without_deadline_never_expire(client):
    """Orders from before the feature (payment_due_at NULL) are never touched."""
    order_id = place_order(client)
    with app.app_context():
        db = store.get_db()
        db.execute("UPDATE orders SET payment_due_at = NULL WHERE id = ?", (order_id,))
        db.commit()
    assert run_expiry(datetime.now(timezone.utc) + timedelta(days=365)) == []
    assert order(order_id)["fulfillment_status"] == "pending"


def test_admin_reopening_removes_the_deadline(client, admin):
    order_id = place_order(client)
    run_expiry(datetime.now(timezone.utc) + LATER)
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "pending"})
    o = order(order_id)
    assert o["fulfillment_status"] == "pending" and o["payment_due_at"] is None
    assert run_expiry(datetime.now(timezone.utc) + LATER) == []
    assert "no automatic cancellation" in admin.html(f"/admin/orders/{order_id}")


def test_concurrent_status_change_wins_over_expiry(client, monkeypatch):
    """The admin marks the order paid between the overdue query and the cancel."""
    order_id = place_order(client)
    real_list = store.list_overdue_unpaid_orders

    def list_then_admin_acts(now, limit=50):
        found = real_list(now, limit)
        store.update_order_fulfillment(
            order_id, fulfillment_status="processing", tracking_number=None, carrier_note=None,
        )
        return found

    monkeypatch.setattr(store, "list_overdue_unpaid_orders", list_then_admin_acts)
    stock_after_checkout = stock_of(2)
    assert run_expiry(datetime.now(timezone.utc) + LATER) == []
    assert order(order_id)["fulfillment_status"] == "processing"
    assert stock_of(2) == stock_after_checkout


def test_customer_sees_the_deadline(make_client):
    c = make_client(locale="tr")
    order_id = place_order(c)
    o = order(order_id)
    confirmation = c.html(f"/checkout/complete/{o['tracking_token']}")
    assert "otomatik olarak iptal edilir" in confirmation
    assert "(TSİ)" in confirmation
    track = c.html(f"/track/{o['tracking_token']}")
    assert "Son ödeme" in track
    # Order time and deadline in the same (Turkish) time, so the window reads as 24 hours
    from services.web import customer_time
    assert customer_time(o["created_at"], "tr") in track
    assert customer_time(o["payment_due_at"], "tr") in track
    [email] = outbox("order_confirmation")
    assert "otomatik olarak iptal edilir" in email["body_text"]


def test_timeline_shows_automatic_cancellation(client):
    order_id = place_order(client)
    run_expiry(datetime.now(timezone.utc) + LATER)
    html = client.html(f"/track/{order(order_id)['tracking_token']}")
    assert "Cancelled automatically: payment was not received in time" in html
    assert "Pay by" not in html  # no deadline once it is no longer pending


def test_housekeeping_runs_the_expiry(client, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(unpaid_orders, "cancel_overdue_orders", lambda: calls.append(1) or [])
    app.config["HOUSEKEEPING_ENABLED"] = True
    app.config["BACKUP_DIR"], previous_dir = str(tmp_path), app.config.get("BACKUP_DIR")
    app_module._housekeeping["next"] = 0.0
    try:
        client.get("/")
        client.get("/products")
    finally:
        app.config["HOUSEKEEPING_ENABLED"] = False
        app.config["BACKUP_DIR"] = previous_dir
        app_module._housekeeping["next"] = 0.0
    assert calls == [1]


def test_turkish_time_is_utc_plus_three():
    from services.web import customer_time

    moment = datetime(2026, 10, 21, 21, 30, tzinfo=timezone.utc)
    assert customer_time(moment, "tr") == "22.10.2026 00:30 (TSİ)"
    assert customer_time(moment, "en") == "2026-10-21 21:30 UTC"
    assert customer_time(None, "tr") == ""
