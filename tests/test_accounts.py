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
