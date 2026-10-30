"""Contact form, owner notification and the admin support inbox."""
import pytest

import services.store as store
from tests.helpers import in_app

VALID = {"name": "Ali Veli", "email": "ali@x.com", "subject": "Order question",
         "message": "When will my order be delivered?", "order_ref": "#12"}


def outbox(kind):
    return [e for e in in_app(store.list_emails, 100) if e["kind"] == kind]


def test_contact_form_stores_message(client):
    r = client.post("/contact", VALID)
    assert r.status_code == 302 and any("received your message" in m for m in client.flashes())
    [msg] = in_app(store.list_support_messages)
    assert msg["email"] == "ali@x.com" and msg["order_ref"] == "#12" and msg["status"] == "open"


def test_contact_form_validation_and_prefill(client):
    r = client.post("/contact", {**VALID, "message": "too short"})
    assert r.status_code == 200 and "at least 10 characters" in r.get_data(as_text=True)
    assert 'value="Ali Veli"' in r.get_data(as_text=True)
    r = client.post("/contact", {**VALID, "email": "nope"})
    assert r.status_code == 200
    assert in_app(store.count_support_messages) == 0
    client.signup(email="member@x.com")
    assert 'value="member@x.com"' in client.html("/contact")


def test_honeypot_pretends_success(client):
    r = client.post("/contact", {**VALID, "website": "http://spam.example"})
    assert r.status_code == 302 and in_app(store.count_support_messages) == 0


def test_contact_rate_limit(client):
    for i in range(5):
        client.post("/contact", {**VALID, "subject": f"Question {i}"})
    r = client.post("/contact", VALID)
    assert r.status_code == 429 and in_app(store.count_support_messages) == 5


@pytest.mark.sales_closed  # starts with the placeholder support address
def test_owner_notified_only_for_a_real_address(client, admin):
    client.post("/contact", VALID)
    assert outbox("support_new") == []  # default placeholder address is skipped
    admin.post("/admin/settings", {"itemsatis_redirect_url": "", "support_email": "owner@shop.test", "support_discord": ""})
    client.post("/contact", {**VALID, "subject": "Second"})
    [note] = outbox("support_new")
    assert note["to_address"] == "owner@shop.test" and "/admin/messages/" in note["body_text"]


def test_admin_reply_emails_customer_in_their_language(make_client, admin):
    tr = make_client(locale="tr")
    tr.post("/contact", VALID)
    msg_id = in_app(store.list_support_messages)[0]["id"]
    assert "Order question" in admin.html("/admin/messages")

    admin.post(f"/admin/messages/{msg_id}", {"reply": "Siparişiniz bugün teslim edilecek.", "close": "1"})
    msg = in_app(store.get_support_message, msg_id)
    assert msg["status"] == "closed" and msg["admin_reply"].startswith("Siparişiniz")
    [reply] = outbox("support_reply")
    assert reply["to_address"] == "ali@x.com" and reply["subject"] == "Re: Order question"
    assert "Mesajınıza yanıtımız" in reply["body_text"] and "> When will my order" in reply["body_text"]
    assert "Order question" not in admin.html("/admin/messages")  # open filter by default
    assert "Order question" in admin.html("/admin/messages?status=closed")

    admin.post(f"/admin/messages/{msg_id}/status", {"status": "open"})
    assert in_app(store.get_support_message, msg_id)["status"] == "open"


def test_empty_reply_rejected_and_inbox_requires_admin(client, admin):
    client.post("/contact", VALID)
    msg_id = in_app(store.list_support_messages)[0]["id"]
    admin.post(f"/admin/messages/{msg_id}", {"reply": "   "})
    assert any("cannot be empty" in m for m in admin.flashes())
    assert outbox("support_reply") == []
    assert client.get("/admin/messages").headers["Location"].endswith("/admin/login")


def test_every_reply_is_kept_in_the_conversation(client, admin):
    client.post("/contact", VALID)
    [msg] = in_app(store.list_support_messages)
    admin.post(f"/admin/messages/{msg['id']}", {"reply": "First answer: it ships today."})
    admin.post(f"/admin/messages/{msg['id']}", {"reply": "Second answer: it has shipped."})
    html = admin.html(f"/admin/messages/{msg['id']}")
    assert "First answer: it ships today." in html and "Second answer: it has shipped." in html
    assert html.index("First answer") < html.index("Second answer")
    assert [e["body_text"].count("Second answer") for e in reversed(outbox("support_reply"))] == [0, 1]


def test_replies_from_before_the_history_table_are_migrated():
    def legacy_reply():
        db = store.get_db()
        db.execute(
            "INSERT INTO support_messages (name, email, subject, message, status, admin_reply, replied_at, created_at) "
            "VALUES ('L', 'l@x.com', 'Old', 'An old question here', 'closed', 'An old answer', '2025-01-02', '2025-01-01')"
        )
        db.commit()
    in_app(legacy_reply)
    from services.database import init_schema
    in_app(init_schema)
    in_app(init_schema)  # idempotent
    [msg] = in_app(store.list_support_messages)
    assert [r["body"] for r in in_app(store.list_support_replies, msg["id"])] == ["An old answer"]


def test_inbox_shows_the_customers_other_messages_and_orders(client, admin):
    client.post("/cart/add/1")
    client.post("/checkout", {"full_name": "Ali", "email": "ali@x.com"})
    client.post("/contact", VALID)
    client.post("/contact", {**VALID, "subject": "Second question"})
    latest = in_app(store.list_support_messages)[0]
    html = admin.html(f"/admin/messages/{latest['id']}")
    assert "Order question" in html  # the earlier message
    order_id = in_app(store.list_orders_admin)[0]["id"]
    assert f"/admin/orders/{order_id}" in html
