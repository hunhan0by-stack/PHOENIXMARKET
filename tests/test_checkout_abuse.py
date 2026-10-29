"""Checkout abuse: stock hoarding and mailbox flooding through guest orders."""
import services.store as store
from tests.helpers import in_app

CANVA = 1


def place(client, email):
    client.post(f"/cart/add/{CANVA}")
    return client.post("/checkout", {"full_name": "Buyer", "email": email})


def test_orders_per_client_ip_are_limited(make_client):
    c = make_client(ip="10.60.0.1")
    for i in range(10):
        assert "/checkout/complete/" in place(c, f"buyer{i}@x.com").headers["Location"]
    r = place(c, "buyer10@x.com")
    assert r.status_code == 429 and in_app(store.count_orders) == 10
    # Another visitor is not affected
    assert "/checkout/complete/" in place(make_client(ip="10.60.0.2"), "other@x.com").headers["Location"]


def test_unpaid_orders_per_email_are_limited(make_client):
    for i in range(3):
        place(make_client(ip=f"10.61.0.{i}"), "Target@X.com")
    r = place(make_client(ip="10.61.0.9"), "target@x.com")
    assert r.status_code == 429 and in_app(store.count_orders) == 3
    assert "already has 3 unpaid orders" in r.get_data(as_text=True)
    # No confirmation email went out for the refused order
    assert len([e for e in in_app(store.list_emails) if e["kind"] == "order_confirmation"]) == 3


def test_paid_or_cancelled_orders_do_not_count(make_client, admin):
    for i in range(3):
        place(make_client(ip=f"10.62.0.{i}"), "regular@x.com")
    first = in_app(store.list_orders_admin)[-1]
    admin.post(f"/admin/orders/{first['id']}/payment", {"payment_ref": ""})
    assert "/checkout/complete/" in place(make_client(ip="10.62.0.8"), "regular@x.com").headers["Location"]
