"""Customer sign-up / sign-in, password changes and order history ownership."""
import services.store as store
from tests.helpers import in_app


def test_signup_validation(client):
    r = client.post("/signup", {"email": "bad-email", "password": "longenough", "confirm_password": "longenough"})
    assert r.status_code == 200 and "valid email" in r.get_data(as_text=True)
    r = client.signup(password="short")
    assert "at least 8" in r.get_data(as_text=True)
    r = client.post("/signup", {"email": "user@x.com", "password": "longenough", "confirm_password": "different1"})
    body = r.get_data(as_text=True)
    assert "do not match" in body and 'value="user@x.com"' in body


def test_signup_creates_hashed_account_and_session(client, make_client):
    r = client.signup(email="User@X.com")
    assert r.status_code == 302 and client.user_id()
    assert client.session()["user"]["email"] == "user@x.com"
    rec = in_app(store.get_user_by_email, "user@x.com")
    assert rec["password_hash"] != "longenough" and rec["password_hash"].count("$") >= 2
    r = make_client(ip="10.1.0.2").signup()
    assert "already exists" in r.get_data(as_text=True)


def test_signin_signout_cycle(client):
    client.signup()
    client.post("/signout")
    assert not client.session().get("user")
    r = client.signin(password="wrong-pass")
    assert "Invalid email or password" in r.get_data(as_text=True)
    r = client.signin(email="USER@x.com")
    assert r.status_code == 302 and r.headers["Location"].endswith("/account")
    html = client.html("/account")
    assert "user@x.com" in html and "Change password" in html


def test_legacy_fake_session_cannot_open_account(client):
    with client.c.session_transaction() as s:
        s["user"] = {"email": "user@x.com"}  # cookie from the old fake login
    r = client.get("/account/orders")
    assert r.status_code == 302 and "/signin" in r.headers["Location"]


def test_change_password(client):
    client.signup()
    client.post("/account/password", {"current_password": "nope", "new_password": "newpass123", "confirm_password": "newpass123"})
    assert any("incorrect" in m for m in client.flashes())
    client.clear_flashes()
    client.post("/account/password", {"current_password": "longenough", "new_password": "short", "confirm_password": "short"})
    assert any("at least 8" in m for m in client.flashes())
    client.clear_flashes()
    client.post("/account/password", {"current_password": "longenough", "new_password": "newpass123", "confirm_password": "newpass123"})
    assert any("updated" in m for m in client.flashes())
    client.post("/signout")
    assert client.signin(password="newpass123").status_code == 302


def test_orders_are_linked_to_the_account_not_the_email(client, make_client):
    client.signup()
    client.post("/cart/add/2")
    assert 'value="user@x.com"' in client.html("/checkout")
    r = client.post("/checkout", {"full_name": "Jane", "email": "not-an-email"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "valid email" in body and 'value="Jane"' in body
    assert client.post("/checkout", {"full_name": "Jane", "email": "user@x.com"}).status_code == 302
    orders = in_app(store.list_orders_for_user, client.user_id())
    assert len(orders) == 1 and orders[0]["user_id"] == client.user_id()

    guest = make_client(ip="10.2.0.1")
    guest.post("/cart/add/2")
    guest.post("/checkout", {"full_name": "Guest", "email": "user@x.com"})
    assert len(in_app(store.list_orders_for_user, client.user_id())) == 1
    html = client.html("/account/orders")
    assert f"#{orders[0]['id']}" in html and "Pending" in html


def test_admin_login_keeps_customer_session(make_client):
    c = make_client(ip="10.3.0.1")
    c.signup()
    c.login_admin()
    s = c.session()
    assert s.get("is_admin") and s["user"]["id"]
    html = c.html("/")
    assert "Admin logout" in html and "Admin Panel" in html


# --- sessions follow the account's password ------------------------------------------
def test_password_change_signs_out_other_sessions(make_client):
    laptop, phone = make_client(), make_client(ip="10.0.0.2")
    laptop.signup()
    phone.signin()
    assert phone.get("/account").status_code == 200
    laptop.post("/account/password", {
        "current_password": "longenough", "new_password": "evenlonger1", "confirm_password": "evenlonger1",
    })
    assert laptop.get("/account").status_code == 200  # the device that changed it stays in
    r = phone.get("/account")
    assert r.status_code == 302 and r.headers["Location"].endswith("/signin")
    assert not phone.session().get("user")


def test_password_reset_signs_out_other_sessions_and_link_works_once(make_client):
    old_device = make_client()
    old_device.signup(email="reset@x.com")
    user_id = old_device.user_id()
    in_app(store.create_password_reset, user_id, "reset-token-1", 3600)
    reset = in_app(store.get_valid_password_reset, "reset-token-1")
    assert in_app(store.complete_password_reset, reset["id"], user_id, "hash-1") is True
    # A second submit of the same link (double click, second tab) changes nothing
    assert in_app(store.complete_password_reset, reset["id"], user_id, "hash-2") is False
    assert in_app(store.get_user, user_id)["password_hash"] == "hash-1"
    assert old_device.get("/account").status_code == 302


def test_sessions_from_before_versioning_stay_valid(client):
    client.signup()
    with client.c.session_transaction() as s:
        s["user"].pop("v")  # cookie issued by the previous release
    assert client.get("/account").status_code == 200


def test_signup_is_rate_limited_per_ip(make_client):
    for n in range(5):
        assert make_client(ip="10.7.0.1").signup(email=f"u{n}@x.com").status_code == 302
    r = make_client(ip="10.7.0.1").signup(email="u5@x.com")
    assert r.status_code == 429 and in_app(store.get_user_by_email, "u5@x.com") is None
    assert make_client(ip="10.7.0.2").signup(email="u6@x.com").status_code == 302
