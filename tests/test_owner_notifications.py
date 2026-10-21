"""Emails to the shop owner: new orders and support messages, in the chosen language."""
import services.notifications as notifications
import services.store as store
from tests.helpers import in_app

OWNER = "owner@shop.test"


def outbox(kind):
    return [e for e in in_app(store.list_emails, 100) if e["kind"] == kind]


def configure(admin, email=OWNER, locale=None):
    data = {"itemsatis_redirect_url": "", "support_email": email, "support_discord": ""}
    if locale:
        data["owner_locale"] = locale
    admin.post("/admin/settings", data)


def place_order(client, email="buyer@x.com"):
    client.post("/cart/add/2")
    r = client.post("/checkout", {"full_name": "Buyer", "email": email, "notes": "Kullanıcı adı: @demo"})
    assert "/checkout/complete/" in r.headers["Location"]
    return in_app(store.list_orders_admin)[0]["id"]


def test_no_owner_email_while_address_is_a_placeholder(client):
    place_order(client)
    assert outbox("owner_new_order") == []
    assert len(outbox("order_confirmation")) == 1


def test_owner_is_told_about_each_new_order(client, admin):
    configure(admin)
    order_id = place_order(client)
    [email] = outbox("owner_new_order")
    assert email["to_address"] == OWNER
    assert email["subject"] == f"New order #{order_id} – $14.99"
    body = email["body_text"]
    assert "buyer@x.com" in body and "Instagram Followers" in body
    assert "Kullanıcı adı: @demo" in body  # the customer's notes (often the delivery details)
    assert f"/admin/orders/{order_id}" in body
    assert "Cancelled automatically if still unpaid at" in body


def test_owner_emails_follow_the_chosen_language(client, admin):
    configure(admin, locale="tr")
    order_id = place_order(client)
    [email] = outbox("owner_new_order")
    assert email["subject"].startswith(f"Yeni sipariş #{order_id}")
    assert "(TSİ)" in email["body_text"]

    client.post("/contact", {
        "name": "Ayşe", "email": "ayse@x.com", "subject": "Teslimat",
        "message": "Siparişim ne zaman gelir?", "order_ref": str(order_id),
    })
    [note] = outbox("support_new")
    assert note["subject"] == "Yeni destek mesajı: Teslimat"
    assert "Gönderen: Ayşe" in note["body_text"]


def test_unknown_language_keeps_the_current_one(admin):
    configure(admin, locale="tr")
    configure(admin, locale="de")
    assert in_app(notifications.owner_locale) == "tr"
    configure(admin)  # field missing: unchanged
    assert in_app(notifications.owner_locale) == "tr"
    assert 'value="tr" selected' in admin.html("/admin/settings")


def test_failing_owner_email_never_loses_the_order(client, admin, monkeypatch):
    configure(admin)

    def boom(order):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(notifications, "send_new_order_owner", boom)
    place_order(client)
    assert in_app(store.count_orders) == 1
    assert len(outbox("order_confirmation")) == 1


def test_admin_email_log_names_the_new_kind(client, admin):
    configure(admin)
    place_order(client)
    assert "New order (to you)" in admin.html("/admin/emails")
