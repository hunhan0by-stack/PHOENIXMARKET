"""Access control: every admin route is closed to visitors and customers; customers see only their own data."""
import re

import pytest

from app import app
import services.store as store
from tests.helpers import in_app

PUBLIC_ADMIN = {"admin.login", "admin.login_verify", "admin.logout"}


def admin_routes():
    """(method, url) for every admin endpoint, with sample values for URL parameters."""
    found = []
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("admin.") or rule.endpoint in PUBLIC_ADMIN:
            continue
        url = re.sub(r"<(?:int:)?[a-z_]+>", "1", rule.rule).replace("<path:filename>", "x")
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            found.append((method, url))
    return found


ROUTES = admin_routes()


def test_route_list_covers_the_panel():
    assert len(ROUTES) > 30 and ("POST", "/admin/orders/1/payment") in ROUTES


@pytest.mark.parametrize("who", ["visitor", "customer"])
def test_every_admin_route_is_closed(make_client, who):
    c = make_client(ip="10.90.0.1")
    if who == "customer":
        c.signup(email="customer@x.com", password="longenough")
    # An order to try and change
    c.post("/cart/add/1")
    c.post("/checkout", {"full_name": "Buyer", "email": "customer@x.com"})
    before = in_app(store.get_order_by_id, 1)
    for method, url in ROUTES:
        r = c.get(url) if method == "GET" else c.post(url, {"payment_ref": "x", "codes": "X", "delivery": "x"})
        assert r.status_code == 302 and r.headers["Location"].endswith("/admin/login"), (method, url)
    after = in_app(store.get_order_by_id, 1)
    assert after["paid_at"] is None and after["fulfillment_status"] == before["fulfillment_status"]
    assert in_app(store.list_code_stock, 1) == {} and in_app(store.list_deliveries, 1) == []


def test_customers_only_see_their_own_orders(make_client):
    alice, bob = make_client(ip="10.91.0.1"), make_client(ip="10.91.0.2")
    alice.signup(email="alice@x.com", password="longenough")
    bob.signup(email="bob@x.com", password="longenough")
    alice.post("/cart/add/1")
    alice.post("/checkout", {"full_name": "Alice Private", "email": "alice@x.com"})
    # The history page links each order's secret tracking page
    token = in_app(store.list_orders_admin)[0]["tracking_token"]
    assert token not in bob.html("/account/orders")
    assert token in alice.html("/account/orders")
    assert token not in bob.get("/account/export").get_data(as_text=True)


def test_tracking_needs_the_exact_secret_token(client):
    client.post("/cart/add/1")
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    token = in_app(store.list_orders_admin)[0]["tracking_token"]
    assert client.get(f"/track/{token}").status_code == 200
    for guess in ("1", token[:-1] + ("0" if token[-1] != "0" else "1"), token.upper()):
        r = client.get(f"/track/{guess}")
        assert r.status_code == 302 and "/track/" not in r.headers["Location"]
    assert client.post(f"/track/{token[:-1]}x/cancel").status_code == 404


def test_errors_do_not_leak_internals(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("secret internal detail /var/data/phoenixmarket.db")

    monkeypatch.setattr(store, "list_public_products", boom)
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    r = client.get("/products")
    body = r.get_data(as_text=True)
    assert r.status_code == 500
    assert "secret internal detail" not in body and "Traceback" not in body and "phoenixmarket.db" not in body
