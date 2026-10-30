"""Attack and abuse scenarios: brute force, replay, races, spoofing and malformed input."""
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

import app as app_module
from app import app
import services.store as store
import services.totp as totp
import services.unpaid_orders as unpaid_orders
from tests.helpers import ADMIN_LOGIN, Client, in_app, set_stock, stock_of

CANVA = 1


def place(client, email="buyer@x.com", qty=1):
    client.post(f"/cart/add/{CANVA}", {"qty": str(qty)})
    return client.post("/checkout", {"full_name": "Buyer", "email": email})


def latest_order():
    return in_app(store.list_orders_admin)[0]


# --- Two-step sign-in ---------------------------------------------------------
def test_two_step_codes_cannot_be_guessed_from_many_addresses(make_client):
    secret = totp.new_secret()
    in_app(totp.enable_admin, secret)
    good = totp.code_at(secret, int(time.time() // 30))
    wrong = "000000" if good != "000000" else "111111"
    # 20 wrong codes, 4 per address: no single address reaches its own limit
    for i in range(5):
        c = make_client(ip=f"10.100.0.{i}")
        c.post("/admin/login", ADMIN_LOGIN)
        for _ in range(4):
            c.post("/admin/login/verify", {"code": wrong})
    fresh = make_client(ip="10.100.1.1")
    fresh.post("/admin/login", ADMIN_LOGIN)
    r = fresh.post("/admin/login/verify", {"code": good})
    assert r.status_code == 429 and fresh.get("/admin/dashboard").status_code == 302
    assert len([e for e in in_app(store.list_security_events) if e["event"] == "admin_2fa_failed"]) == 20


# --- Password reset -----------------------------------------------------------
def test_changing_the_password_burns_open_reset_links(client):
    client.signup(email="owner@x.com", password="original-pass")
    user_id = client.user_id()
    in_app(store.create_password_reset, user_id, "leaked-token", 3600)
    client.post("/account/password", {
        "current_password": "original-pass", "new_password": "brand-new-pass", "confirm_password": "brand-new-pass",
    })
    assert in_app(store.get_valid_password_reset, "leaked-token") is None
    r = Client(ip="10.101.0.2").get("/reset-password/leaked-token")
    assert r.headers["Location"].endswith("/forgot-password")


# --- Orders and deliveries ----------------------------------------------------
def test_customer_cannot_cancel_after_receiving_a_delivery(client, admin):
    place(client)
    order = latest_order()
    admin.post(f"/admin/orders/{order['id']}/delivery", {"delivery": "user: a / pass: b"})
    stock_before = stock_of(CANVA)
    client.post(f"/track/{order['tracking_token']}/cancel")
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "pending"
    assert stock_of(CANVA) == stock_before


def test_payment_deadline_does_not_cancel_delivered_orders(client, admin):
    place(client)
    order = latest_order()
    admin.post(f"/admin/orders/{order['id']}/delivery", {"delivery": "details"})
    place(Client(ip="10.102.0.2"), email="other@x.com")
    later = datetime.now(timezone.utc) + timedelta(days=3)
    cancelled = in_app(unpaid_orders.cancel_overdue_orders, later)
    assert order["id"] not in cancelled and len(cancelled) == 1


def test_retrying_automatic_delivery_after_codes_ran_short(client, admin):
    set_stock(CANVA, 5)
    admin.post(f"/admin/listings/{CANVA}/codes", {"codes": "ONLY-ONE"})
    place(client, qty=2)
    order = latest_order()
    retry = f"/admin/orders/{order['id']}/auto-deliver"

    admin.post(retry)  # unpaid: refused
    assert in_app(store.count_codes_for_order, order["id"]) == 0
    admin.post(f"/admin/orders/{order['id']}/payment", {"payment_ref": "P"})  # too few codes
    assert in_app(store.count_codes_for_order, order["id"]) == 0

    admin.post(f"/admin/listings/{CANVA}/codes", {"codes": "SECOND"})
    admin.clear_flashes()
    admin.post(retry)
    assert in_app(store.count_codes_for_order, order["id"]) == 2
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "completed"
    admin.post(retry)  # a second retry hands out nothing more
    assert in_app(store.count_codes_for_order, order["id"]) == 2
    assert any("Nothing to deliver" in m for m in admin.flashes())


def test_simultaneous_checkouts_cannot_oversell_the_last_unit(make_client):
    set_stock(CANVA, 1)
    clients = [make_client(ip=f"10.103.0.{i}") for i in range(4)]
    for c in clients:
        c.post(f"/cart/add/{CANVA}")
    barrier = threading.Barrier(len(clients))

    def buy(c, i):
        barrier.wait()
        c.post("/checkout", {"full_name": "Racer", "email": f"racer{i}@x.com"})

    threads = [threading.Thread(target=buy, args=(c, i)) for i, c in enumerate(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert in_app(store.count_orders) == 1 and stock_of(CANVA) == 0


# --- Rate limits behind the proxy ---------------------------------------------
def test_spoofed_forwarded_for_does_not_evade_checkout_limits(monkeypatch):
    """With the production proxy settings the address Render appends (last) counts, not one the client adds."""
    monkeypatch.setattr(app, "wsgi_app", ProxyFix(app.wsgi_app, **app_module.PROXY_FIX))
    c = Client(ip="10.104.0.1")
    for i in range(11):
        c.post(f"/cart/add/{CANVA}")
        r = c.post(
            "/checkout", {"full_name": "Spoofer", "email": f"spoof{i}@x.com"},
            headers={"X-Forwarded-For": f"203.0.113.{i}, 198.51.100.7"},
        )
    assert r.status_code == 429 and in_app(store.count_orders) == 10


# --- Uploaded files -----------------------------------------------------------
def test_only_image_names_are_served_from_the_upload_folder(client):
    folder = app.config["UPLOAD_FOLDER"]
    with open(f"{folder}/phoenixmarket.db", "wb") as f:
        f.write(b"SQLite format 3\x00 not for visitors")
    with open(f"{folder}/listing.jpg", "wb") as f:
        f.write(b"\xff\xd8\xff fake jpeg")
    try:
        assert client.get("/uploads/phoenixmarket.db").status_code == 404
        assert client.get("/uploads/..%2Fphoenixmarket.db").status_code == 404
        assert client.get("/uploads/listing.jpg").status_code == 200
    finally:
        import os
        os.remove(f"{folder}/phoenixmarket.db")
        os.remove(f"{folder}/listing.jpg")


# --- Malformed input never causes a server error ------------------------------
@pytest.mark.parametrize("method, path, data", [
    ("post", "/cart/add/1", {"qty": "-5"}),
    ("post", "/cart/add/1", {"qty": "1e999"}),
    ("post", "/cart/add/1", {"qty": "99999999999999999999"}),
    ("post", "/cart/add/1", {"variant_id": "1 OR 1=1"}),
    ("post", "/cart/add/99999", {}),
    ("post", "/cart/update/abc:def", {"qty": "2"}),
    ("post", "/cart/update/1:-3", {"qty": "x"}),
    ("post", "/cart/coupon", {"code": "' OR '1'='1"}),
    ("get", "/track/" + "a" * 5000, None),
    ("get", "/product/%00", None),
    ("get", "/category/<script>", None),
    ("get", "/search?q=" + "%27%22%3E%3Cscript%3E" * 50, None),
    ("get", "/legal/..%2F..%2Fapp.py", None),
    ("get", "/admin/backups/..%2F..%2Fphoenixmarket.db", None),
    ("post", "/contact", {"name": "x" * 10000, "email": "a@b.c", "subject": "s", "message": "m" * 100000}),
])
def test_malformed_requests_are_handled(make_client, method, path, data):
    c = make_client(ip="10.105.0.1")
    r = c.get(path) if method == "get" else c.post(path, data)
    assert r.status_code < 500, (path, r.status_code)
    body = r.get_data(as_text=True)
    assert "Traceback" not in body and "<script>" not in body.replace("&lt;script&gt;", "")


@pytest.mark.parametrize("payload", [None, [], "text", {"message": 123}, {"message": ["a"]}, {"message": "x" * 5000}])
def test_chat_api_rejects_malformed_json(client, payload):
    r = client.post_json("/api/chat", payload)
    assert r.status_code in (400, 503) and "error" in r.get_json()


def test_checkout_with_tampered_quote_or_fields_is_refused(client):
    client.post(f"/cart/add/{CANVA}")
    r = client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com", "quote": "0" * 64})
    assert r.status_code == 200 and in_app(store.count_orders) == 0
    r = client.post("/checkout", {"full_name": "Buyer", "email": "not-an-email"})
    assert in_app(store.count_orders) == 0


# --- Headers and error pages --------------------------------------------------
def test_error_pages_and_api_keep_security_headers(client):
    for path in ("/definitely-missing", "/admin/login"):
        r = client.get(path)
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'self'" in r.headers["Content-Security-Policy"]
    r = client.post_json("/api/chat", {"message": "hi"})
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_tracking_page_is_not_cached_or_indexed_and_hides_internal_notes(client, admin):
    place(client)
    order = latest_order()
    admin.post(f"/admin/orders/{order['id']}/notes", {"note": "INTERNAL fraud check"})
    r = client.get(f"/track/{order['tracking_token']}")
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Robots-Tag"] == "noindex"
    assert "INTERNAL fraud check" not in r.get_data(as_text=True)
    assert re.search(r"Referrer-Policy", str(r.headers))
