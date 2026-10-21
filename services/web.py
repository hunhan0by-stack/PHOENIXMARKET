"""
Request/session helpers shared by the views: locale and currency, translated
flash messages, the signed-in customer, the admin flag, CSRF tokens and safe
"go back" redirects.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta, timezone
from urllib.parse import urlparse

from flask import current_app, g, redirect, request, session, url_for
from markupsafe import Markup

import services.store as store
from services.translations import t as translate

LOCALE_KEY = "locale"
CURRENCY_KEY = "currency"
SUPPORTED_LOCALES = ("en", "tr")
SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

CSRF_SESSION_KEY = "_csrf_token"


# Crawlers, uptime monitors and scripts: not counted as visitors
BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|preview|monitor|uptime|headless|lighthouse|"
    r"curl|wget|python-|httpx|go-http|java/|okhttp|axios|node-fetch|scrapy",
    re.IGNORECASE,
)


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(BOT_UA_RE.search(user_agent))


def visitor_id(ip: str, day: str) -> str:
    """Pseudonymous visitor id: keyed hash of the IP and the UTC day.

    The IP itself is never stored, and because the day is part of the input
    the same person cannot be followed from one day to the next.
    """
    key = current_app.config["SECRET_KEY"].encode("utf-8")
    return hmac.new(key, f"{day}|{ip}".encode("utf-8"), hashlib.sha256).hexdigest()[:16]


# Turkey keeps UTC+3 all year (no daylight saving since 2016)
TURKEY_TIME = timezone(timedelta(hours=3))


def customer_time(dt, locale):
    """A moment as customers read it: Turkish time for TR, UTC otherwise.

    Takes the locale explicitly (not from the session) because order emails
    are also rendered outside the customer's own request.
    """
    if not dt:
        return ""
    if locale == "tr":
        return dt.astimezone(TURKEY_TIME).strftime("%d.%m.%Y %H:%M") + " (TSİ)"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def delivery_label(hours, locale):
    """"Delivered within 24 hours" / "3 gün içinde teslim"; whole days from 48 hours."""
    if not hours:
        return ""
    if hours == 1:
        return translate("delivery_within_one_hour", locale)
    if hours >= 48 and hours % 24 == 0:
        return translate("delivery_within_days", locale).format(n=hours // 24)
    return translate("delivery_within_hours", locale).format(n=hours)


def get_current_locale():
    """Get user's locale from session, default en."""
    loc = session.get(LOCALE_KEY) or "en"
    return loc if loc in SUPPORTED_LOCALES else "en"


def get_current_currency():
    """Get user's currency from session, default USD."""
    cur = session.get(CURRENCY_KEY) or "USD"
    return cur if cur in SUPPORTED_CURRENCIES else "USD"


def tr_msg(key: str, **kwargs) -> str:
    """Translate a message for the current locale, filling {placeholders}."""
    return translate(key, get_current_locale()).format(**kwargs)


def current_user():
    """
    The signed-in customer ({"id", "email"}), or None. The session is checked
    against the account once per request: a deleted account or a password
    changed elsewhere (session_version bumped) signs this session out.
    """
    if "current_user" in g:
        return g.current_user
    user = session.get("user") or {}
    result = None
    if user.get("id"):
        record = store.get_user(user["id"])
        if record and record.get("session_version", 0) == user.get("v", 0):
            result = {"id": record["id"], "email": record["email"]}
        else:
            session.pop("user", None)
    g.current_user = result
    return result


def login_customer(user: dict) -> None:
    """Sign a customer in (user: a users row, or at least id and email)."""
    session["user"] = {"id": user["id"], "email": user["email"], "v": user.get("session_version", 0)}
    g.pop("current_user", None)


def logout_customer() -> None:
    session.pop("user", None)
    g.pop("current_user", None)


def is_admin():
    user = session.get("user") or {}
    # Support both the older "admin" flag and the new "is_admin" flag
    if session.get("is_admin"):
        return True
    return bool(session.get("admin")) and user.get("role") == "admin"


def password_problem(password: str, confirm: str):
    """Translation key describing why a new password is unacceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return "flash_password_too_short"
    if password != confirm:
        return "flash_passwords_mismatch"
    return None


def client_ip() -> str:
    return (
        request.remote_addr
        or (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        or "unknown"
    )


def safe_back(fallback_endpoint: str = "shop.index", **values):
    """Redirect to the same-site referrer, else to fallback_endpoint."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(url_for(fallback_endpoint, **values))


def safe_next(target: str | None, fallback_url: str) -> str:
    """target if it is a local path ("/..."), else fallback_url (no open redirects)."""
    target = (target or "").strip()
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return fallback_url


# -----------------------------------------------------------------------------
# CSRF protection: every state-changing request must echo the session token,
# as a "csrf_token" form field or an "X-CSRF-Token" header (used by chat.js).
# The check itself runs in app.py (before_request).
# -----------------------------------------------------------------------------
def get_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_field():
    return Markup('<input type="hidden" name="csrf_token" value="{}">').format(
        get_csrf_token()
    )


def csp_nonce() -> str:
    """Per-request nonce for the few inline <script> blocks (see app.security_headers)."""
    if "csp_nonce" not in g:
        g.csp_nonce = secrets.token_urlsafe(16)
    return g.csp_nonce


def content_security_policy() -> str:
    script_src = "'self'"
    if "csp_nonce" in g:
        script_src += f" 'nonce-{g.csp_nonce}'"
    return "; ".join([
        "default-src 'self'",
        f"script-src {script_src}",
        "style-src 'self'",
        "img-src 'self' data: https:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'self'",
    ])


def csrf_token_valid() -> bool:
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get(CSRF_SESSION_KEY) or ""
    return bool(expected) and secrets.compare_digest(sent.encode(), expected.encode())
