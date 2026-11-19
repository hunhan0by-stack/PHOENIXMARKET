"""CSRF protection, security headers, redirect safety and brute-force limits."""
import re

import services.store as store
from tests.helpers import in_app


def test_post_without_or_with_wrong_token_is_rejected(client):
    r = client.post("/cart/add/2", csrf=False)
    assert r.status_code == 302
    assert "2" not in (client.session().get("cart") or {})
    assert any("session expired" in m for m in client.flashes())
    client.post("/cart/add/2", {"csrf_token": "wrong"})
    assert "2" not in (client.session().get("cart") or {})
    client.post("/cart/add/2")
    assert client.session()["cart"]["2"] == 1


def test_api_requires_csrf_header(client):
    r = client.post_json("/api/chat", {"message": "hi"}, header_token=None)
    assert r.status_code == 400 and "error" in r.get_json()


def test_pages_render_csrf_meta_and_fields(client):
    html = client.html("/")
    assert 'name="csrf-token"' in html and 'name="csrf_token"' in html


def test_external_referrer_is_not_followed(client):
    r = client.get("/set-locale/tr", headers={"Referer": "https://evil.example/phish"})
    assert r.headers["Location"] in ("/", "http://localhost/")
    r = client.get("/set-locale/tr", headers={"Referer": "http://localhost/products"})
    assert r.headers["Location"].endswith("/products")


def test_signin_locks_after_ten_failures_per_ip(make_client):
    make_client(ip="10.1.0.9").signup()
    attacker = make_client(ip="10.66.0.1")
    for _ in range(10):
        attacker.signin(password="bad-password")
    r = attacker.signin()
    assert r.status_code == 429 and "Too many attempts" in r.get_data(as_text=True)
    assert make_client(ip="10.66.0.2").signin().status_code == 302


def test_signing_in_to_an_own_account_does_not_clear_failed_guesses(make_client):
    make_client(ip="10.1.0.10").signup(email="victim@x.com")
    attacker = make_client(ip="10.66.1.1")
    attacker.signup(email="attacker@x.com")
    attacker.post("/signout")
    for _ in range(9):
        attacker.signin(email="victim@x.com", password="bad-password")
    assert attacker.signin(email="attacker@x.com").status_code == 302
    attacker.post("/signout")
    attacker.signin(email="victim@x.com", password="bad-password")
    r = attacker.signin(email="victim@x.com")
    assert r.status_code == 429 and not attacker.session().get("user")


def test_signin_locks_an_account_guessed_from_many_ips(make_client):
    make_client(ip="10.1.0.11").signup(email="target@x.com")
    make_client(ip="10.1.0.12").signup(email="other@x.com")
    for n in range(20):
        make_client(ip=f"10.67.0.{n + 1}").signin(email="target@x.com", password="bad-password")
    fresh = make_client(ip="10.67.1.1")
    assert fresh.signin(email="target@x.com").status_code == 429
    assert fresh.signin(email="other@x.com").status_code == 302


def test_signup_counts_attempts_with_registered_emails(make_client):
    make_client(ip="10.1.0.13").signup(email="taken@x.com")
    prober = make_client(ip="10.68.0.1")
    for _ in range(10):
        r = prober.signup(email="taken@x.com")
        assert r.status_code == 200
    r = prober.signup(email="new-account@x.com")
    assert r.status_code == 429 and in_app(store.get_user_by_email, "new-account@x.com") is None


def test_admin_login_locks_after_five_failures(make_client):
    attacker = make_client(ip="10.77.0.1")
    for _ in range(5):
        attacker.post("/admin/login", {"username": "admin", "password": "nope"})
    r = attacker.post("/admin/login", {"username": "admin", "password": "admin-pass"})
    assert r.status_code == 429 and not attacker.session().get("is_admin")


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in r.headers["Permissions-Policy"]


def test_content_security_policy_allows_only_own_scripts_and_styles(client):
    r = client.get("/")
    csp = r.headers["Content-Security-Policy"]
    html = r.get_data(as_text=True)
    assert "script-src 'self';" in csp and "style-src 'self';" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "cdn." not in csp
    assert "<style" not in html and 'style="' not in html  # nothing inline to allow
    assert "onsubmit=" not in html and "onclick=" not in html
    assert '/static/css/app.css?v=' in html
    assert "Content-Security-Policy" not in client.get("/robots.txt").headers


def test_admin_pages_require_login(client):
    for path in ("/admin/dashboard", "/admin/listings", "/admin/orders", "/admin/settings"):
        r = client.get(path)
        assert r.status_code == 302 and r.headers["Location"].endswith("/admin/login")
    r = client.post("/admin/create-category", {"name": "Hacked"})
    assert r.headers["Location"].endswith("/admin/login")


def test_sign_out_needs_a_csrf_checked_post(client):
    """A GET (old bookmark, forged <img src>) or a cross-site POST cannot sign anyone out."""
    client.signup()
    assert client.get("/signout").status_code == 302
    assert client.user_id()
    client.post("/signout", csrf=False)
    assert client.user_id()
    client.post("/signout")
    assert not client.user_id()


def test_admin_logout_needs_a_csrf_checked_post(admin):
    r = admin.get("/admin/logout")
    assert r.headers["Location"].endswith("/admin/dashboard")
    assert admin.session().get("is_admin")
    admin.post("/admin/logout", csrf=False)
    assert admin.session().get("is_admin")
    admin.post("/admin/logout")
    assert not admin.session().get("is_admin")


def test_sign_out_controls_are_forms(client, admin):
    client.signup()
    for html in (client.html("/"), client.html("/account"), admin.html("/admin/dashboard")):
        assert 'href="/signout"' not in html and 'href="/admin/logout"' not in html
    assert 'action="/signout"' in client.html("/account")
    assert 'action="/admin/logout"' in admin.html("/admin/dashboard")


def test_static_assets_are_versioned_and_cached(client):
    html = client.html("/")
    for asset in ("css/app.css", "js/app.js", "js/chat.js", "images/logo-64.png"):
        url = re.search(rf'"(/static/{re.escape(asset)}\?v=[0-9a-f]{{10}})"', html)
        assert url, asset
        r = client.get(url.group(1))
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    # Without a version the browser revalidates daily
    assert client.get("/static/images/canva.jpg").headers["Cache-Control"] == "public, max-age=86400"
    # Pages themselves are not cached as static files
    assert "immutable" not in (client.get("/").headers.get("Cache-Control") or "")
