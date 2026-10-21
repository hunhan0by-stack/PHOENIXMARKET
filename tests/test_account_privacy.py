"""KVKK self-service: download the account's data, delete the account."""
import json

import services.store as store
from tests.helpers import in_app

EMAIL, PASSWORD = "veri@x.com", "uzunsifre1"


def signed_up_with_history(make_client, ip="10.40.0.1"):
    c = make_client(ip=ip)
    c.signup(email=EMAIL, password=PASSWORD)
    c.post("/cart/add/2")
    c.post("/checkout", {"full_name": "Veri Sahibi", "email": EMAIL, "notes": "kullanıcı: @veri"})
    c.post("/wishlist/1")
    c.post("/contact", {"name": "Veri", "email": EMAIL, "subject": "Soru", "message": "Bir sorum var, yardımcı olur musunuz?"})
    return c


def test_export_contains_only_the_accounts_data(make_client):
    # A guest order with the same email address, placed by someone else
    guest = make_client(ip="10.40.0.9")
    guest.post("/cart/add/3")
    guest.post("/checkout", {"full_name": "Başkası", "email": EMAIL})
    c = signed_up_with_history(make_client)
    r = c.get("/account/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["Cache-Control"] == "no-store"
    data = json.loads(r.data)
    assert data["account"]["email"] == EMAIL
    assert [o["full_name"] for o in data["orders"]] == ["Veri Sahibi"]  # not the guest order
    assert data["orders"][0]["items"][0]["name"] == "Instagram Followers"
    assert data["favourites"] == ["Canva Pro Subscription"]
    assert data["support_messages"][0]["subject"] == "Soru"
    assert "password" not in r.get_data(as_text=True).lower()


def test_export_requires_sign_in(client):
    r = client.get("/account/export")
    assert r.status_code == 302 and r.headers["Location"].endswith("/signin")


def test_delete_needs_password_and_confirmation(make_client):
    c = signed_up_with_history(make_client)
    c.post("/account/delete", {"password": "wrong-password", "confirm_delete": "1"})
    assert in_app(store.get_user_by_email, EMAIL)
    c.post("/account/delete", {"password": PASSWORD})
    assert in_app(store.get_user_by_email, EMAIL)
    assert any("cannot be undone" in m for m in c.flashes())


def test_delete_removes_personal_data_and_keeps_orders_unlinked(make_client):
    c = signed_up_with_history(make_client)
    user_id = c.user_id()
    order_id = in_app(store.list_orders_admin)[0]["id"]
    token = in_app(store.get_order_by_id, order_id)["tracking_token"]
    r = c.post("/account/delete", {"password": PASSWORD, "confirm_delete": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert not c.user_id()
    assert in_app(store.get_user_by_email, EMAIL) is None
    assert in_app(store.wishlist_product_ids, user_id) == set()
    order = in_app(store.get_order_by_id, order_id)
    assert order and order["user_id"] is None  # kept as a commercial record
    assert in_app(store.get_support_message, in_app(store.list_support_messages)[0]["id"])["user_id"] is None
    assert c.get(f"/track/{token}").status_code == 200
    # The email can be used for a new account
    assert make_client(ip="10.40.0.2").signup(email=EMAIL, password=PASSWORD).status_code == 302


def test_delete_attempts_are_rate_limited(make_client):
    c = signed_up_with_history(make_client)
    for _ in range(10):
        c.post("/account/delete", {"password": "nope", "confirm_delete": "1"})
    c.clear_flashes()
    c.post("/account/delete", {"password": PASSWORD, "confirm_delete": "1"})
    assert in_app(store.get_user_by_email, EMAIL)  # blocked even with the right password
    assert any("Too many" in m for m in c.flashes())


def test_delete_requires_csrf(make_client):
    c = signed_up_with_history(make_client)
    c.post("/account/delete", {"password": PASSWORD, "confirm_delete": "1"}, csrf=False)
    assert in_app(store.get_user_by_email, EMAIL)
