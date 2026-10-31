"""Tracking and password-reset links: the secret never reaches the visit log, the log file, caches or other sites."""
import logging
import re
import sys

import pytest
from flask import Flask

import services.sensitive_urls as sensitive_urls
import services.store as store
from app import app
from tests.helpers import in_app
from views.admin.core import mask_visit_paths

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
BROWSER = {"User-Agent": CHROME}
INSTAGRAM = 2

# Route variables known to hold no secret. A new variable name that is neither
# here nor named like a secret (services/sensitive_urls.SECRET_WORDS) fails
# test_every_route_variable_is_classified: decide which one it is.
HARMLESS_VARIABLES = {
    "product_id", "variant_id", "category_id", "coupon_id", "order_id", "code_id", "email_id",
    "message_id", "review_id", "slug_or_id", "category_slug", "page", "locale", "currency",
    "key",  # cart line (product/package ids)
    "filename", "name",  # static files, listing images, admin backup file names
}


def visit_paths():
    return in_app(lambda: [r["path"] for r in store.get_db().execute("SELECT path FROM visits ORDER BY id")])


def place_order(client):
    client.post(f"/cart/add/{INSTAGRAM}")
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    order = in_app(store.list_orders_admin)[0]
    return in_app(store.get_order_by_id, order["id"])["tracking_token"]


def reset_token(make_client):
    user = make_client(ip="10.30.0.1")
    user.signup(email="reset@x.com", password="oldpassword")
    make_client(ip="10.30.0.2").post("/forgot-password", {"email": "reset@x.com"})
    [email] = [e for e in in_app(store.list_emails, 100) if e["kind"] == "password_reset"]
    return re.search(r"/reset-password/(\S+)", email["body_text"]).group(1)


def mask(path):
    with app.app_context():
        return sensitive_urls.mask(path)


# --- the masking rule -------------------------------------------------------------
@pytest.mark.parametrize("path, masked", [
    ("/track/3f2b9c1e-8a7d-4e21-b0c4-1d2e3f4a5b6c", "/track/<token>"),
    ("/track/abc/cancel", "/track/<token>/cancel"),
    ("/track/abc/anything-else", "/track/<token>/anything-else"),  # mistyped link (404)
    ("/checkout/complete/abc", "/checkout/complete/<token>"),
    ("/reset-password/Zx9_-Qw", "/reset-password/<token>"),
])
def test_secret_parts_are_masked(path, masked):
    assert mask(path) == masked
    with app.app_context():
        assert sensitive_urls.is_sensitive(path)


@pytest.mark.parametrize("path", [
    "/", "/products", "/product/instagram-followers", "/category/social-media", "/legal/privacy",
    "/cart", "/checkout", "/track", "/trackx/abc", "/forgot-password", "/account",
])
def test_ordinary_pages_are_unchanged(path):
    assert mask(path) == path


def test_the_three_secret_routes_are_recognised():
    rules = {r.rule for r in app.url_map.iter_rules() if any(map(sensitive_urls.is_secret_name, r.arguments))}
    assert {"/track/<token>", "/checkout/complete/<token>", "/reset-password/<token>", "/track/<token>/cancel"} <= rules


def test_every_route_variable_is_classified():
    """Guards future routes: a variable must be named like a secret or be known to be harmless."""
    unknown = {
        (rule.rule, name)
        for rule in app.url_map.iter_rules() for name in rule.arguments
        if not sensitive_urls.is_secret_name(name) and name not in HARMLESS_VARIABLES
    }
    assert not unknown, f"Classify these route variables (secret name or HARMLESS_VARIABLES): {unknown}"


def test_a_new_secret_route_is_covered_without_changes():
    other = Flask("future")
    other.add_url_rule("/gift/<int:gift_id>/claim/<claim_token>", "claim", lambda **_: "")
    other.add_url_rule("/invite/<string:invite_secret>", "invite", lambda **_: "")
    with other.app_context():
        assert sensitive_urls.mask("/gift/7/claim/s3cr3t") == "/gift/7/claim/<claim_token>"
        assert sensitive_urls.mask("/invite/s3cr3t") == "/invite/<invite_secret>"
        assert sensitive_urls.mask("/gift/7") == "/gift/7"


# --- visit statistics -------------------------------------------------------------
def test_secret_pages_are_counted_without_the_secret(make_client, client):
    token = place_order(client)
    reset = reset_token(make_client)
    visitor = make_client(ip="10.40.0.1")
    for path in (f"/checkout/complete/{token}", f"/track/{token}", f"/reset-password/{reset}",
                 "/track/not-a-real-token", f"/track/{token}/x", "/reset-password/expired"):
        visitor.get(path, headers=BROWSER)
    visitor.get("/products", headers=BROWSER)

    paths = visit_paths()
    assert token not in str(paths) and reset not in str(paths) and "not-a-real-token" not in str(paths)
    assert paths == [
        "/checkout/complete/<token>", "/track/<token>", "/reset-password/<token>",
        "/track/<token>", "/track/<token>/x", "/reset-password/<token>", "/products",
    ]


def test_dashboard_masks_visits_recorded_before_the_fix_without_changing_them(admin):
    legacy = "/track/3f2b9c1e-8a7d-4e21-b0c4-1d2e3f4a5b6c"

    def insert():
        db = store.get_db()
        for path in (legacy, "/reset-password/old-secret", "/track/other-old-token", "/products"):
            db.execute(
                "INSERT INTO visits (ts, path, ip, user_agent) VALUES ('2026-10-01T10:00:00+00:00', ?, 'abcdef0123456789', 'Chrome')",
                (path,),
            )
        db.commit()
    in_app(insert)

    html = admin.html("/admin/dashboard")
    assert "3f2b9c1e" not in html and "old-secret" not in html and "other-old-token" not in html
    assert "/track/&lt;token&gt;</span> — " in html and "/reset-password/&lt;token&gt;" in html
    report = in_app(lambda: mask_visit_paths(store.get_analytics_for_admin()))
    assert ("/track/<token>", 2) in report["top_pages"]  # both old tracking rows counted together
    assert visit_paths() == [legacy, "/reset-password/old-secret", "/track/other-old-token", "/products"]  # rows unchanged


# --- headers ----------------------------------------------------------------------
def test_secret_pages_are_not_cached_indexed_or_sent_as_referer_to_other_sites(make_client, client):
    token = place_order(client)
    reset = reset_token(make_client)
    for path in (f"/checkout/complete/{token}", f"/track/{token}", f"/reset-password/{reset}", "/track/unknown"):
        r = client.get(path)
        assert r.headers["Cache-Control"] == "no-store", path
        assert r.headers["Referrer-Policy"] == "same-origin", path
        assert r.headers["X-Robots-Tag"] == "noindex", path
        assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_ordinary_pages_keep_the_usual_headers(client):
    r = client.get("/products")
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "no-store" not in r.headers.get("Cache-Control", "") and "X-Robots-Tag" not in r.headers


def test_language_switch_still_returns_to_the_tracking_page(client):
    """same-origin (not no-referrer) keeps the switch working on secret pages."""
    token = place_order(client)
    r = client.get("/set-locale/tr", headers={"Referer": f"http://localhost/track/{token}"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/track/{token}")


# --- itemsatis buttons, session, log ----------------------------------------------
def test_itemsatis_buttons_send_no_referer_and_no_opener(client):
    token = place_order(client)
    for page in (f"/checkout/complete/{token}", f"/track/{token}"):
        anchors = re.findall(r'<a href="https://www\.itemsatis\.com[^"]*" rel="([^"]*)"', client.html(page))
        assert anchors and all(set(rel.split()) >= {"noopener", "noreferrer"} for rel in anchors), page


def test_the_tracking_token_is_not_copied_into_the_session_cookie(client):
    token = place_order(client)
    assert token not in str(client.session())


def test_logged_errors_name_the_page_without_the_secret(caplog):
    with app.test_request_context("/track/3f2b9c1e-8a7d-4e21-b0c4-1d2e3f4a5b6c"), caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            app.log_exception(sys.exc_info())
    assert "Exception on /track/<token> [GET]" in caplog.text and "3f2b9c1e" not in caplog.text
