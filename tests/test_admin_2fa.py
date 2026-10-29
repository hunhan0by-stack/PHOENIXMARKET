"""Admin two-step sign-in (TOTP) and the idle sign-out of the admin session."""
import time

import pytest

from app import app
import services.store as store
import services.totp as totp
from tests.helpers import ADMIN_LOGIN, in_app

# RFC 6238 appendix B: the ASCII secret "12345678901234567890"
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


@pytest.mark.parametrize("unix_time, code", [
    (59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037"),
])
def test_rfc_6238_vectors(unix_time, code):
    assert totp.code_at(RFC_SECRET, unix_time // 30) == code
    assert totp.matching_step(RFC_SECRET, code, now=unix_time) == unix_time // 30


def test_codes_are_accepted_one_step_either_side_only():
    now = 1234567890
    step = now // 30
    assert totp.matching_step(RFC_SECRET, totp.code_at(RFC_SECRET, step - 1), now=now) == step - 1
    assert totp.matching_step(RFC_SECRET, totp.code_at(RFC_SECRET, step + 2), now=now) is None
    assert totp.matching_step(RFC_SECRET, "12345", now=now) is None
    assert totp.matching_step(RFC_SECRET, "abcdef", now=now) is None


def current_code(secret, offset=0):
    return totp.code_at(secret, int(time.time() // 30) + offset)


@pytest.fixture
def secret():
    s = totp.new_secret()
    in_app(totp.enable_admin, s)
    return s


def test_password_alone_is_not_enough(make_client, secret):
    c = make_client(ip="10.50.0.1")
    r = c.post("/admin/login", ADMIN_LOGIN)
    assert r.headers["Location"].endswith("/admin/login/verify")
    assert c.get("/admin/dashboard").headers["Location"].endswith("/admin/login")
    assert "authenticator app" in c.html("/admin/login/verify")


def test_correct_code_signs_in_and_cannot_be_replayed(make_client, secret):
    code = current_code(secret)
    c = make_client(ip="10.50.0.2")
    c.post("/admin/login", ADMIN_LOGIN)
    r = c.post("/admin/login/verify", {"code": code[:3] + " " + code[3:]})
    assert r.headers["Location"].endswith("/admin/dashboard")
    assert c.get("/admin/dashboard").status_code == 200

    # The same code seen by someone else is useless
    other = make_client(ip="10.50.0.3")
    other.post("/admin/login", ADMIN_LOGIN)
    r = other.post("/admin/login/verify", {"code": code})
    assert r.status_code == 200 and "already used" in r.get_data(as_text=True)
    assert other.get("/admin/dashboard").status_code == 302


def test_wrong_codes_are_rate_limited(make_client, secret):
    c = make_client(ip="10.50.0.4")
    c.post("/admin/login", ADMIN_LOGIN)
    wrong = "000000" if current_code(secret) != "000000" else "111111"
    for _ in range(5):
        c.post("/admin/login/verify", {"code": wrong})
    r = c.post("/admin/login/verify", {"code": current_code(secret)})
    assert r.status_code == 429


def test_code_step_expires(make_client, secret):
    c = make_client(ip="10.50.0.5")
    c.post("/admin/login", ADMIN_LOGIN)
    with c.c.session_transaction() as s:
        s["admin_2fa_pending"] = time.time() - 6 * 60
    r = c.post("/admin/login/verify", {"code": current_code(secret)})
    assert r.headers["Location"].endswith("/admin/login")


def test_verify_page_needs_the_password_first(client, secret):
    r = client.post("/admin/login/verify", {"code": current_code(secret)})
    assert r.headers["Location"].endswith("/admin/login")


def test_setting_up_two_step_sign_in(admin):
    assert "Two-step sign-in is off" in admin.html("/admin/dashboard")
    html = admin.html("/admin/security")
    assert "<svg" in html and 'class="segno"' in html
    secret = in_app(store.get_config, totp.PENDING_KEY)
    assert secret and secret in html
    # The secret is kept on the server, never in the (readable) session cookie
    assert secret not in str(admin.session())

    admin.post("/admin/security/enable", {"code": "000000" if current_code(secret) != "000000" else "111111"})
    assert not in_app(totp.admin_enabled)
    admin.post("/admin/security/enable", {"code": current_code(secret)})
    assert in_app(totp.admin_secret) == secret
    assert not in_app(store.get_config, totp.PENDING_KEY)
    # The admin who turned it on stays signed in
    assert "Two-step sign-in is off" not in admin.html("/admin/dashboard")


def test_turning_off_needs_a_code(make_client, secret):
    admin = make_client(ip="10.50.0.6")
    admin.post("/admin/login", ADMIN_LOGIN)
    admin.post("/admin/login/verify", {"code": current_code(secret)})
    admin.post("/admin/security/disable", {"code": ""})
    assert in_app(totp.admin_enabled)
    # The sign-in used this step's code; the next one is still accepted
    admin.post("/admin/security/disable", {"code": current_code(secret, offset=1)})
    assert not in_app(totp.admin_enabled)
    assert admin.get("/admin/dashboard").status_code == 200  # this session stays signed in


def test_cli_turns_off_two_step_sign_in(secret):
    result = app.test_cli_runner().invoke(args=["disable-admin-2fa"])
    assert result.exit_code == 0 and "now off" in result.output
    assert not in_app(totp.admin_enabled)


def test_turning_on_two_step_signs_out_other_admin_sessions(admin, make_client):
    other = make_client(ip="10.50.0.9")
    other.login_admin()  # e.g. a stolen cookie
    secret = in_app(totp.pending_secret)
    admin.post("/admin/security/enable", {"code": current_code(secret)})
    assert admin.get("/admin/dashboard").status_code == 200
    assert other.get("/admin/dashboard").headers["Location"].endswith("/admin/login")


def test_changing_the_admin_password_revokes_sessions(admin, monkeypatch):
    assert admin.get("/admin/dashboard").status_code == 200
    monkeypatch.setitem(app.config, "ADMIN_PASSWORD", "a-brand-new-password")
    assert admin.get("/admin/dashboard").headers["Location"].endswith("/admin/login")


def test_admin_session_has_an_absolute_lifetime(admin):
    with admin.c.session_transaction() as s:
        s["admin_since"] = time.time() - 8 * 24 * 60 * 60
    assert admin.get("/admin/dashboard").headers["Location"].endswith("/admin/login")


def test_session_without_credential_fingerprint_is_not_admin(client):
    # Old-style cookie (before the fingerprint existed) or a forged flag
    with client.c.session_transaction() as s:
        s["is_admin"] = True
    assert client.get("/admin/dashboard").headers["Location"].endswith("/admin/login")


def test_idle_admin_is_signed_out(admin):
    assert admin.get("/admin/dashboard").status_code == 200
    with admin.c.session_transaction() as s:
        s["admin_seen"] = time.time() - 13 * 60 * 60
    assert admin.get("/admin/dashboard").headers["Location"].endswith("/admin/login")
    assert "is_admin" not in admin.session()
