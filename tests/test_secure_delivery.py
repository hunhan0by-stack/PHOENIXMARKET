"""Delivered details: on the tracking page only, never emailed, cleared after the retention period."""
import json
from datetime import datetime, timedelta, timezone

import app as app_module
from app import app
import services.store as store
from tests.helpers import in_app

SECRET = "user: phoenix@mail.test\npass: S3cret-Pa55"


def place_order(client, email="buyer@x.com"):
    client.post("/cart/add/1")
    client.post("/checkout", {"full_name": "Buyer", "email": email})
    order = in_app(store.list_orders_admin)[0]
    return in_app(store.get_order_by_id, order["id"])


def deliver(admin, order_id, **form):
    data = {"delivery": SECRET, "complete": "1", "notify_customer": "1"}
    data.update(form)
    return admin.post(f"/admin/orders/{order_id}/delivery", data)


def test_delivery_is_shown_on_the_tracking_page_and_completes_the_order(client, admin):
    order = place_order(client)
    r = deliver(admin, order["id"])
    assert r.headers["Location"].endswith(f"/admin/orders/{order['id']}#delivery")
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "completed"

    page = client.get(f"/track/{order['tracking_token']}")
    html = page.get_data(as_text=True)
    assert "S3cret-Pa55" in html and "Order delivered" in html
    assert page.headers["Cache-Control"] == "no-store"
    assert page.headers["X-Robots-Tag"] == "noindex"
    assert "S3cret-Pa55" in admin.html(f"/admin/orders/{order['id']}")


def test_details_never_reach_the_email_outbox_or_timeline(client, admin):
    order = place_order(client)
    deliver(admin, order["id"])
    emails = in_app(store.list_emails)
    ready = [e for e in emails if e["kind"] == "delivery_ready"]
    assert len(ready) == 1 and order["tracking_token"] in ready[0]["body_text"]
    assert all("S3cret" not in (e["body_text"] + (e["body_html"] or "")) for e in emails)
    assert all("S3cret" not in (e["note"] or "") for e in in_app(store.list_order_events, order["id"]))


def test_delivery_can_leave_status_and_skip_the_email(client, admin):
    order = place_order(client)
    deliver(admin, order["id"], complete="", notify_customer="")
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "pending"
    assert not [e for e in in_app(store.list_emails) if e["kind"] == "delivery_ready"]


def test_empty_or_cancelled_deliveries_are_refused(client, admin):
    order = place_order(client)
    deliver(admin, order["id"], delivery="   ")
    assert in_app(store.list_deliveries, order["id"]) == []
    in_app(
        store.update_order_fulfillment, order["id"],
        fulfillment_status="cancelled", tracking_number=None, carrier_note=None,
    )
    deliver(admin, order["id"])
    assert in_app(store.list_deliveries, order["id"]) == []


def test_details_are_cleared_after_the_retention_period(client, admin):
    order = place_order(client)
    deliver(admin, order["id"])
    days = app.config["DELIVERY_RETENTION_DAYS"]
    now = datetime.now(timezone.utc)
    assert in_app(store.purge_expired_deliveries, now + timedelta(days=days - 1)) == 0
    assert in_app(store.purge_expired_deliveries, now + timedelta(days=days, minutes=1)) == 1
    [d] = in_app(store.list_deliveries, order["id"])
    assert d["body"] is None and d["purged_at"]
    html = client.html(f"/track/{order['tracking_token']}")
    assert "S3cret" not in html and "removed from this page for your security" in html


def test_housekeeping_clears_expired_details(client, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(store, "purge_expired_deliveries", lambda: calls.append(1) or 0)
    app.config["HOUSEKEEPING_ENABLED"] = True
    app.config["BACKUP_DIR"], previous_dir = str(tmp_path), app.config.get("BACKUP_DIR")
    app_module._housekeeping["next"] = 0.0
    try:
        client.get("/")
    finally:
        app.config["HOUSEKEEPING_ENABLED"] = False
        app.config["BACKUP_DIR"] = previous_dir
        app_module._housekeeping["next"] = 0.0
    assert calls == [1]


def test_account_export_and_deletion(make_client, admin):
    c = make_client(ip="10.42.0.1")
    c.signup(email="veri@x.com", password="uzunsifre1")
    order = place_order(c, email="veri@x.com")
    deliver(admin, order["id"])
    data = json.loads(c.get("/account/export").data)
    assert data["orders"][0]["deliveries"][0]["details"] == SECRET

    c.post("/account/delete", {"password": "uzunsifre1", "confirm_delete": "1"})
    assert in_app(store.get_user_by_email, "veri@x.com") is None
    [d] = in_app(store.list_deliveries, order["id"])
    assert d["body"] is None
