"""Checkout gate (option B): no new order until the seller details are complete and the current texts reviewed."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

import services.legal as legal
import services.store as store
import services.unpaid_orders as unpaid_orders
from tests.helpers import TEST_SELLER, in_app, open_shop_for_sales, stock_of

CANVA = 1
SELLER_FORM = {f"seller_{k}": v for k, v in TEST_SELLER.items()}


def close_by(reason):
    """Make one requirement fail on an otherwise open shop."""
    if reason == "email":
        in_app(store.set_config, "site_settings.support_email", "contact@phoenixmarket.example")
    elif reason == legal.NOT_REVIEWED:
        in_app(legal.set_texts_reviewed, False)
    else:
        in_app(legal.save_seller_info, {**TEST_SELLER, reason: ""})


def coupon(code="GATE10"):
    in_app(store.create_coupon, {
        "code": code, "kind": "percent", "value": 10, "min_order_usd": 0, "max_uses": 5,
        "starts_at": None, "expires_at": None, "active": True,
    })
    return code


def outbox_count():
    return in_app(store.count_emails)


@pytest.mark.parametrize("reason", ["name", "address", "tax_id", "email", legal.NOT_REVIEWED])
def test_each_missing_requirement_closes_sales(make_client, reason):
    close_by(reason)
    assert reason in in_app(legal.checkout_blockers)
    c = make_client(locale="tr", ip="10.140.0.1")
    c.post(f"/cart/add/{CANVA}")
    code = coupon()
    c.post("/cart/coupon", {"code": code})
    stock, emails = stock_of(CANVA), outbox_count()

    page = c.html("/checkout")
    assert "Şu anda yeni sipariş alamıyoruz" in page and 'name="full_name"' not in page
    r = c.post("/checkout", {"full_name": "Ayşe", "email": "ayse@x.test"})
    assert r.status_code == 503 and "Sepetiniz korunuyor" in r.get_data(as_text=True)

    assert in_app(store.count_orders) == 0             # no order
    assert stock_of(CANVA) == stock                     # no stock reserved
    assert in_app(store.get_coupon_by_code, code)["used_count"] == 0  # no coupon use
    assert outbox_count() == emails                     # no email
    assert c.session()["cart"] == {str(CANVA): 1}       # cart kept
    # The customer is not told which internal setting is missing
    body = r.get_data(as_text=True)
    assert "tax_id" not in body and "texts_reviewed" not in body and "henüz girilmedi" not in body


@pytest.mark.sales_closed
def test_fresh_shop_starts_closed_and_opens_when_both_conditions_hold(make_client, admin):
    assert set(in_app(legal.checkout_blockers)) == {"name", "address", "tax_id", "email", legal.NOT_REVIEWED}
    c = make_client(ip="10.140.0.2")
    c.post(f"/cart/add/{CANVA}")
    assert c.post("/checkout", {"full_name": "A", "email": "a@x.test"}).status_code == 503

    admin.post("/admin/settings", {"itemsatis_redirect_url": "", "support_email": "owner@shop.test", "support_discord": ""})
    admin.post("/admin/settings/legal", SELLER_FORM)  # details only, not reviewed
    assert in_app(legal.checkout_blockers) == [legal.NOT_REVIEWED]
    assert c.post("/checkout", {"full_name": "A", "email": "a@x.test"}).status_code == 503

    admin.post("/admin/settings/legal", {**SELLER_FORM, "texts_reviewed": "1"})
    assert in_app(legal.checkout_blockers) == []
    r = c.post("/checkout", {"full_name": "A", "email": "a@x.test"})
    assert "/checkout/complete/" in r.headers["Location"] and in_app(store.count_orders) == 1


def test_review_of_an_older_version_does_not_count(client, monkeypatch):
    # Reviewed under an earlier version of the texts
    in_app(store.set_config, legal.REVIEWED_KEY, "2000-01-01")
    assert legal.NOT_REVIEWED in in_app(legal.checkout_blockers)
    client.post(f"/cart/add/{CANVA}")
    assert client.post("/checkout", {"full_name": "A", "email": "a@x.test"}).status_code == 503
    # Reviewed now, then the texts change (LEGAL_VERSION bumped in a new release)
    in_app(legal.set_texts_reviewed, True)
    monkeypatch.setattr(legal, "LEGAL_VERSION", "2099-01-01")
    assert client.post("/checkout", {"full_name": "A", "email": "a@x.test"}).status_code == 503
    assert in_app(store.count_orders) == 0


def test_cart_shows_the_notice_instead_of_the_checkout_button(make_client):
    c = make_client(locale="tr", ip="10.140.0.3")
    c.post(f"/cart/add/{CANVA}")
    assert "Şu anda yeni sipariş alamıyoruz" not in c.html("/cart")
    close_by(legal.NOT_REVIEWED)
    cart = c.html("/cart")
    assert "Şu anda yeni sipariş alamıyoruz" in cart and 'href="/checkout"' not in cart


def test_dashboard_shows_sales_closed_with_the_missing_items(admin):
    assert "Sales are temporarily closed" not in admin.html("/admin/dashboard")
    close_by("tax_id")
    close_by(legal.NOT_REVIEWED)
    html = admin.html("/admin/dashboard")
    assert "Sales are temporarily closed" in html and "have not been marked as reviewed" in html
    with admin.c.session_transaction() as s:
        s["locale"] = "tr"
    tr = admin.html("/admin/dashboard")
    assert "Satışlar geçici olarak kapalı" in tr


def test_existing_orders_keep_working_while_sales_are_closed(make_client, admin):
    admin.post(f"/admin/listings/{CANVA}/codes", {"codes": "GATE-CODE-1"})
    c = make_client(ip="10.140.0.4")
    for email in ("paid@x.test", "cancel@x.test", "expire@x.test"):
        c.post(f"/cart/add/{CANVA}")
        c.post("/checkout", {"full_name": "A", "email": email})
    orders = {o["user_email"]: o for o in in_app(store.list_orders_admin)}
    close_by("name")  # sales close after these orders were placed

    paid = orders["paid@x.test"]
    admin.post(f"/admin/orders/{paid['id']}/payment", {"payment_ref": "P1", "notify_customer": "1"})
    paid = in_app(store.get_order_by_id, paid["id"])
    assert paid["paid_at"] and paid["fulfillment_status"] == "completed"   # payment + automatic delivery
    assert "GATE-CODE-1" in c.html(f"/track/{paid['tracking_token']}")    # tracking + digital delivery
    assert any(e["kind"] == "delivery_ready" for e in in_app(store.list_emails))  # notifications

    cancel = orders["cancel@x.test"]
    c.post(f"/track/{cancel['tracking_token']}/cancel")                     # customer cancellation
    assert in_app(store.get_order_by_id, cancel["id"])["fulfillment_status"] == "cancelled"

    expired = in_app(unpaid_orders.cancel_overdue_orders, datetime.now(timezone.utc) + timedelta(days=2))
    assert expired == [orders["expire@x.test"]["id"]]                        # automatic expiry


@pytest.mark.sales_closed
@pytest.mark.parametrize("who", ["visitor", "customer"])
def test_only_the_admin_can_open_sales(make_client, who):
    c = make_client(ip="10.140.0.5")
    if who == "customer":
        c.signup(email="sneaky@x.test", password="longenough")
    r = c.post("/admin/settings/legal", {**SELLER_FORM, "texts_reviewed": "1"})
    assert r.headers["Location"].endswith("/admin/login")
    c.post("/admin/settings", {"support_email": "sneaky@x.test"})
    assert in_app(legal.checkout_blockers) != []
    c.post(f"/cart/add/{CANVA}")
    assert c.post("/checkout", {"full_name": "A", "email": "a@x.test"}).status_code == 503


@pytest.mark.sales_closed
def test_admin_cannot_be_tricked_into_opening_sales_without_csrf(admin):
    r = admin.post("/admin/settings/legal", {**SELLER_FORM, "texts_reviewed": "1"}, csrf=False)
    assert r.status_code in (302, 400)
    assert in_app(legal.texts_reviewed) is False and in_app(legal.seller_info)["name"] == ""


def test_simultaneous_checkouts_while_closed_create_nothing(make_client):
    close_by(legal.NOT_REVIEWED)
    stock = stock_of(CANVA)
    clients = [make_client(ip=f"10.141.0.{i}") for i in range(5)]
    for c in clients:
        c.post(f"/cart/add/{CANVA}")
    barrier, statuses = threading.Barrier(len(clients)), []

    def buy(c, i):
        barrier.wait()
        statuses.append(c.post("/checkout", {"full_name": "R", "email": f"r{i}@x.test"}).status_code)

    threads = [threading.Thread(target=buy, args=(c, i)) for i, c in enumerate(clients)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert statuses == [503] * 5
    assert in_app(store.count_orders) == 0 and stock_of(CANVA) == stock


def test_sales_reopen_after_completing_the_requirements(client):
    close_by("address")
    client.post(f"/cart/add/{CANVA}")
    assert client.post("/checkout", {"full_name": "A", "email": "a@x.test"}).status_code == 503
    open_shop_for_sales()
    r = client.post("/checkout", {"full_name": "A", "email": "a@x.test"})
    assert "/checkout/complete/" in r.headers["Location"]
