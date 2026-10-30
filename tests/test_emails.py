"""Outbox, SMTP delivery, order emails and the password reset flow."""
import re
import smtplib

import pytest

from app import app
import services.store as store
from tests.helpers import in_app


def outbox(kind=None):
    emails = in_app(store.list_emails, 100)
    return [e for e in emails if kind is None or e["kind"] == kind]


def place_order(client, email="buyer@x.com"):
    client.post("/cart/add/2")
    return client.post("/checkout", {"full_name": "Buyer", "email": email})


class FakeSMTP:
    sent = []
    fail = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        if FakeSMTP.fail:
            raise smtplib.SMTPException("relay refused")
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.fail = [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("MAIL_FROM", "shop@pm.test")
    monkeypatch.setitem(app.config, "MAIL_SYNC", True)
    return FakeSMTP


def test_checkout_logs_confirmation_in_customer_language(make_client):
    c = make_client(locale="tr")
    place_order(c)
    [email] = outbox("order_confirmation")
    order = in_app(store.list_orders_admin)[0]
    assert email["status"] == "logged" and email["to_address"] == "buyer@x.com"
    assert email["subject"] == f"PHOENIXMARKET siparişiniz #{order['id']}"
    assert f"http://localhost/track/{order['tracking_token']}" in email["body_text"]
    assert "https://itemsatis.test/shop" in email["body_text"]
    assert "Instagram Followers" in email["body_html"]


def test_smtp_delivery_and_failure(client, smtp):
    place_order(client)
    [email] = outbox("order_confirmation")
    assert email["status"] == "sent" and email["sent_at"]
    # The shop owner is notified too (sales need a real support address)
    [message] = [m for m in smtp.sent if m["To"] == "buyer@x.com"]
    assert message["To"] == "buyer@x.com" and message["From"] == "shop@pm.test"
    assert message.get_body(("html",)) is not None

    smtp.fail = True
    place_order(client, email="second@x.com")
    failed = [e for e in outbox("order_confirmation") if e["to_address"] == "second@x.com"][0]
    assert failed["status"] == "failed" and "relay refused" in failed["error"]
    # The order itself is kept even though the email failed.
    assert in_app(store.count_orders) == 2


def test_admin_update_emails_only_when_requested_and_changed(client, admin):
    place_order(client)
    order_id = in_app(store.list_orders_admin)[0]["id"]
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "processing"})
    assert outbox("order_update") == []
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "completed", "tracking_number": "T-1", "notify_customer": "1"})
    [update] = outbox("order_update")
    assert "Status changed to Completed" in update["body_text"]
    assert "Tracking number added: T-1" in update["body_text"]
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "completed", "tracking_number": "T-1", "notify_customer": "1"})
    assert len(outbox("order_update")) == 1  # nothing changed, nothing sent


def reset_link(email):
    return re.search(r"http://localhost(/reset-password/\S+)", email["body_text"]).group(1)


def test_password_reset_flow(make_client):
    user = make_client(ip="10.20.0.1")
    user.signup(email="reset@x.com", password="oldpassword")
    user.post("/signout")

    anon = make_client(ip="10.20.0.2")
    r = anon.post("/forgot-password", {"email": "nobody@x.com"})
    assert r.status_code == 302 and any("If an account exists" in m for m in anon.flashes())
    assert outbox("password_reset") == []

    anon.post("/forgot-password", {"email": "Reset@X.com"})
    [email] = outbox("password_reset")
    path = reset_link(email)
    assert anon.get(path).status_code == 200

    r = anon.post(path, {"password": "newpassword1", "confirm_password": "mismatch1"})
    assert "do not match" in r.get_data(as_text=True)
    r = anon.post(path, {"password": "newpassword1", "confirm_password": "newpassword1"})
    assert r.status_code == 302 and anon.user_id()

    r = anon.get(path)  # single use
    assert r.status_code == 302 and r.headers["Location"].endswith("/forgot-password")
    anon.post("/signout")
    assert anon.signin(email="reset@x.com", password="newpassword1").status_code == 302


def test_expired_reset_link_is_rejected(client):
    client.signup(email="late@x.com")
    user_id = client.user_id()
    in_app(store.create_password_reset, user_id, "expired-token", -1)
    r = client.get("/reset-password/expired-token")
    assert r.status_code == 302 and any("expired" in m for m in client.flashes())


def test_reset_request_limits(make_client):
    make_client(ip="10.21.0.1").signup(email="limit@x.com")
    for i in range(4):
        make_client(ip=f"10.22.0.{i}").post("/forgot-password", {"email": "limit@x.com"})
    assert len(outbox("password_reset")) == 3  # per-account cap

    spammer = make_client(ip="10.23.0.1")
    for _ in range(5):
        spammer.post("/forgot-password", {"email": "x@x.com"})
    assert spammer.post("/forgot-password", {"email": "x@x.com"}).status_code == 429


def test_admin_outbox_pages(client, admin):
    place_order(client)
    html = admin.html("/admin/emails")
    assert "buyer@x.com" in html and "logged" in html and "SMTP is not configured" in html
    email_id = outbox("order_confirmation")[0]["id"]
    detail = admin.html(f"/admin/emails/{email_id}")
    assert "/track/" in detail
    assert client.get("/admin/emails").headers["Location"].endswith("/admin/login")


def test_order_emails_keep_dark_design_and_have_preview_text(make_client):
    place_order(make_client(locale="tr"))
    [email] = outbox("order_confirmation")
    html = email["body_html"]
    assert '<meta name="color-scheme" content="dark">' in html
    order_id = in_app(store.list_orders_admin)[0]["id"]
    assert f"Sipariş #{order_id}: ödeme bilgileri ve takip bağlantınız" in html
    assert "display:none" in html.split("Sipariş #", 1)[0][-200:]  # preview line is hidden
