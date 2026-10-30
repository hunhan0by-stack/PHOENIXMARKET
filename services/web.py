"""
Request/session helpers shared by the views: locale and currency, translated
flash messages, the signed-in customer, the admin flag, CSRF tokens and safe
"go back" redirects.
"""
import hashlib
import hmac
import re
import secrets
import time
from datetime import timedelta, timezone
from urllib.parse import urlencode, urlparse

from flask import current_app, g, has_request_context, redirect, request, session, url_for
from markupsafe import Markup

import services.store as store
import services.totp as totp
from services.translations import t as translate

LOCALE_KEY = "locale"
CURRENCY_KEY = "currency"
SUPPORTED_LOCALES = ("en", "tr")
SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8

# The admin is signed out after this long without opening an admin page;
# ADMIN_SEEN_KEY holds the time of the last one (refreshed by the admin blueprint)
ADMIN_IDLE_SECONDS = 12 * 60 * 60
ADMIN_SEEN_KEY = "admin_seen"
# ... and at the latest this long after signing in, however active
ADMIN_MAX_SECONDS = 7 * 24 * 60 * 60
ADMIN_SINCE_KEY = "admin_since"
ADMIN_EPOCH_KEY = "admin_epoch"

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


def default_locale() -> str:
    """Language for visitors without a choice (and for search engines): DEFAULT_LOCALE, Turkish when unset."""
    loc = current_app.config.get("DEFAULT_LOCALE") or "tr"
    return loc if loc in SUPPORTED_LOCALES else "tr"


def get_current_locale():
    """?lang= on the URL, else the visitor's saved choice, else the default language.

    The URL parameter makes each language a crawlable address (see
    localized_url and the hreflang links in base.html).
    """
    loc = request.args.get("lang") if has_request_context() else None
    if loc not in SUPPORTED_LOCALES:
        loc = session.get(LOCALE_KEY)
    return loc if loc in SUPPORTED_LOCALES else default_locale()


def localized_url(locale: str, keep_query: bool = False, switch: bool = False) -> str:
    """Absolute URL of the current page in a language: plain for the default
    language, ?lang=xx otherwise (the canonical form used by hreflang/sitemap).

    switch=True always adds ?lang= (a language switch must override a saved choice).
    """
    args = request.args.to_dict(flat=False) if keep_query else {}
    args.pop("lang", None)
    if switch or locale != default_locale():
        args["lang"] = [locale]
    query = urlencode(args, doseq=True)
    return request.base_url + (f"?{query}" if query else "")


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


def admin_epoch() -> str:
    """Fingerprint of the admin's credentials (password and two-step secret).

    Stored in the session at sign-in: changing ADMIN_PASSWORD or turning
    two-step sign-in on/off changes it and so signs out every other admin
    session (signed cookies cannot be revoked otherwise).
    """
    if "admin_epoch" not in g:
        material = "\0".join([
            current_app.config.get("ADMIN_USERNAME") or "",
            current_app.config.get("ADMIN_PASSWORD") or "",
            totp.admin_secret() or "",
        ])
        g.admin_epoch = hmac.new(
            str(current_app.config["SECRET_KEY"]).encode("utf-8"), material.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return g.admin_epoch


def login_admin() -> None:
    now = time.time()
    session["is_admin"] = True
    session["admin"] = True
    session[ADMIN_SEEN_KEY] = now
    session[ADMIN_SINCE_KEY] = now
    session[ADMIN_EPOCH_KEY] = admin_epoch()


def refresh_admin_epoch() -> None:
    """After the signed-in admin changed the credentials: keep this session, revoke the others."""
    g.pop("admin_epoch", None)
    session[ADMIN_EPOCH_KEY] = admin_epoch()


def is_admin():
    user = session.get("user") or {}
    # Support both the older "admin" flag and the new "is_admin" flag
    signed_in = bool(session.get("is_admin")) or (bool(session.get("admin")) and user.get("role") == "admin")
    if not signed_in:
        return False
    now = time.time()
    if (
        now - session.get(ADMIN_SEEN_KEY, now) > ADMIN_IDLE_SECONDS
        or now - session.get(ADMIN_SINCE_KEY, now) > ADMIN_MAX_SECONDS
        or not hmac.compare_digest(str(session.get(ADMIN_EPOCH_KEY, "")), admin_epoch())
    ):
        # Idle or too old, or the credentials changed since sign-in: the
        # panel asks for the login again
        logout_admin()
        return False
    return True


def logout_admin() -> None:
    for key in ("is_admin", "admin", ADMIN_SEEN_KEY, ADMIN_SINCE_KEY, ADMIN_EPOCH_KEY):
        session.pop(key, None)


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


def is_http_url(url: str, max_length: int) -> bool:
    """An absolute http(s) link without spaces, at most max_length characters."""
    parsed = urlparse(url)
    return (
        parsed.scheme in ("http", "https") and bool(parsed.netloc)
        and len(url) <= max_length and not any(ch.isspace() for ch in url)
    )


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
