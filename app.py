import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, flash, g, has_request_context, jsonify, render_template, request, session, url_for
from flask.logging import default_handler
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

import services.backup as backup
from services.currency import format_price
from services.database import init_app as register_db, init_schema
from services.listing import (
    get_display_price,
    format_product_price,
    get_listing_language_badge,
)
import services.store as store
from services.translations import t as translate
import services.unpaid_orders as unpaid_orders
from services.uploads import product_image_url
from services.web import (
    LOCALE_KEY,
    MIN_PASSWORD_LENGTH,
    SUPPORTED_CURRENCIES,
    SUPPORTED_LOCALES,
    client_ip,
    content_security_policy,
    csp_nonce,
    csrf_field,
    csrf_token_valid,
    current_user,
    customer_time,
    delivery_label,
    get_csrf_token,
    get_current_currency,
    get_current_locale,
    is_admin,
    is_bot,
    localized_url,
    safe_back,
    tr_msg,
    visitor_id,
)
from views import register_blueprints
from views.api import chat_api_key

load_dotenv()

app = Flask(__name__)

# -----------------------------------------------------------------------------
# Base configuration – environment driven for production readiness
# -----------------------------------------------------------------------------
app_root = Path(__file__).resolve().parent

# On Render, require SECRET_KEY and ITEMSATIS_REDIRECT_URL; allow dev defaults locally
if os.getenv("RENDER"):
    secret_key = os.getenv("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable is required on Render. "
            "Set it in your Render service Environment tab."
        )
    itemsatis_url = os.getenv("ITEMSATIS_REDIRECT_URL")
    if not itemsatis_url:
        raise RuntimeError(
            "ITEMSATIS_REDIRECT_URL environment variable is required on Render. "
            "Set it in your Render service Environment tab."
        )
else:
    secret_key = os.getenv("SECRET_KEY", "dev-secret-change-in-production")
    itemsatis_url = os.getenv("ITEMSATIS_REDIRECT_URL", "https://example.com/itemsatis")

app.config["SECRET_KEY"] = secret_key
app.config["ITEMSATIS_REDIRECT_URL"] = itemsatis_url

# Listing uploads. Point UPLOAD_DIR at a persistent disk in production (e.g. a
# Render disk) – files there are served from /uploads/<name>.
UPLOAD_FOLDER = Path(os.getenv("UPLOAD_DIR") or (app_root / "static" / "uploads"))
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB request limit (image uploads)

# Admin credentials: required in production (Render or FLASK_ENV=production); dev defaults otherwise.
_IS_PRODUCTION = bool(os.getenv("RENDER") or os.getenv("FLASK_ENV") == "production")
if _IS_PRODUCTION:
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        raise RuntimeError(
            "ADMIN_USERNAME and ADMIN_PASSWORD environment variables are required in production. "
            "Set them in Render (or use FLASK_ENV=production locally only with real secrets)."
        )
else:
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "phoenixmarket")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
app.config["ADMIN_USERNAME"] = ADMIN_USERNAME
app.config["ADMIN_PASSWORD"] = ADMIN_PASSWORD

# -----------------------------------------------------------------------------
# Data directory & SQLite
# -----------------------------------------------------------------------------
DATA_DIR = app_root / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH", str(DATA_DIR / "phoenixmarket.db"))
# Language for first-time visitors and search engines ("en" or "tr")
app.config["DEFAULT_LOCALE"] = (os.getenv("DEFAULT_LOCALE") or "en").strip().lower()

# Daily database copies; default is a "backups" folder next to the database
app.config["BACKUP_DIR"] = os.getenv("BACKUP_DIR") or None

# Hours a customer has to pay before an unpaid order is cancelled and its
# stock released; 0 turns automatic cancellation off.
try:
    app.config["PENDING_ORDER_TTL_HOURS"] = max(
        float(os.getenv("PENDING_ORDER_TTL_HOURS") or unpaid_orders.DEFAULT_PAYMENT_WINDOW_HOURS), 0.0
    )
except ValueError:
    app.logger.warning("PENDING_ORDER_TTL_HOURS is not a number; using the default")
    app.config["PENDING_ORDER_TTL_HOURS"] = float(unpaid_orders.DEFAULT_PAYMENT_WINDOW_HOURS)

register_db(app)
with app.app_context():
    init_schema()
    store.seed_if_empty(itemsatis_url)
    store.migrate_listing_images_png_to_jpg()
    store.maybe_migrate_analytics_json(DATA_DIR)
    # Raw IPs / user agents recorded before anonymisation (no-op afterwards)
    store.anonymize_legacy_visits(visitor_id)
    _persisted = store.load_site_settings(
        itemsatis_url,
        "contact@phoenixmarket.example",
        "YourDiscordTag#0000",
    )
    if _persisted.get("itemsatis_redirect_url"):
        app.config["ITEMSATIS_REDIRECT_URL"] = _persisted["itemsatis_redirect_url"]

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.getenv("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

register_blueprints(app)
# A global (not a context processor) so order emails rendered outside the
# customer's request can use it too
app.jinja_env.globals["customer_time"] = customer_time
app.jinja_env.globals["delivery_label"] = delivery_label
app.jinja_env.globals["localized_url"] = localized_url

# Static files referenced with asset_url() carry a content hash (?v=…), so
# browsers may cache them for a year; a changed file gets a new URL.
_asset_hashes: dict = {}
STATIC_IMMUTABLE = "public, max-age=31536000, immutable"
STATIC_DEFAULT = "public, max-age=86400"


def asset_url(filename: str) -> str:
    path = app_root / "static" / filename
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return url_for("static", filename=filename)
    cached = _asset_hashes.get(filename)
    if not cached or cached[0] != mtime:
        cached = (mtime, hashlib.sha256(path.read_bytes()).hexdigest()[:10])
        _asset_hashes[filename] = cached
    return url_for("static", filename=filename, v=cached[1])


app.jinja_env.globals["asset_url"] = asset_url


@app.context_processor
def inject_globals():
    """Inject common marketplace-wide data into templates."""
    cart = session.get("cart") or {}
    cart_count = sum(cart.values())
    locale = get_current_locale()
    currency = get_current_currency()
    user = current_user()

    def t(key):
        return translate(key, locale)

    site_settings = store.load_site_settings(
        app.config.get("ITEMSATIS_REDIRECT_URL", ""),
        "contact@phoenixmarket.example",
        "YourDiscordTag#0000",
    )
    return {
        "cart_count": cart_count,
        "site_content": store.load_site_content(locale),
        "site_settings": site_settings,
        "locale": locale,
        "currency": currency,
        "t": t,
        "format_price": format_price,
        "get_display_price": lambda p: get_display_price(p, currency),
        "format_product_price": lambda p: format_product_price(p, currency),
        "get_listing_language_badge": get_listing_language_badge,
        "product_image_url": product_image_url,
        "current_user": user,
        "favorite_ids": store.wishlist_product_ids(user["id"]) if user else frozenset(),
        "is_admin": is_admin(),
        "csrf_token": get_csrf_token,
        "csrf_field": csrf_field,
        "csp_nonce": csp_nonce,
        "chat_api_enabled": chat_api_key() is not None,
        "LOW_STOCK_THRESHOLD": store.LOW_STOCK_THRESHOLD,
        "MIN_PASSWORD_LENGTH": MIN_PASSWORD_LENGTH,
        "SUPPORTED_LOCALES": SUPPORTED_LOCALES,
        "SUPPORTED_CURRENCIES": SUPPORTED_CURRENCIES,
    }


# -----------------------------------------------------------------------------
# Logging: every line carries the request id, which the error page shows the
# customer ("error code"), so a report can be matched to the log (Render logs).
# -----------------------------------------------------------------------------
class _RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = g.get("request_id", "-") if has_request_context() else "-"
        return True


default_handler.addFilter(_RequestIdFilter())
default_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
)
if not app.debug:
    app.logger.setLevel(logging.INFO)


@app.before_request
def assign_request_id():
    g.request_id = secrets.token_hex(4).upper()


@app.before_request
def remember_language_from_url():
    """Opening a ?lang=xx address (language switch, search result) keeps that language."""
    lang = request.args.get("lang")
    if request.method == "GET" and lang in SUPPORTED_LOCALES and session.get(LOCALE_KEY) != lang:
        session[LOCALE_KEY] = lang


# Periodic maintenance rides on incoming requests (Render's health checks keep
# it going without visitors): Render web services have no scheduler.
HOUSEKEEPING_INTERVAL_SECONDS = 10 * 60
_housekeeping = {"next": 0.0}


@app.before_request
def housekeeping():
    if not app.config.get("HOUSEKEEPING_ENABLED", True) or request.path.startswith("/static/"):
        return
    now = time.monotonic()
    if now < _housekeeping["next"]:
        return
    _housekeeping["next"] = now + HOUSEKEEPING_INTERVAL_SECONDS
    backup.maybe_auto_backup()
    try:
        store.purge_old_visits()
    except Exception:  # noqa: BLE001
        app.logger.exception("Purging old visits failed")
    if unpaid_orders.payment_window_hours() > 0:
        try:
            unpaid_orders.cancel_overdue_orders()
        except Exception:  # noqa: BLE001 - maintenance never breaks the visitor's request
            app.logger.exception("Cancelling unpaid orders failed")


@app.cli.command("backup")
def backup_command():
    """Write a database backup now (e.g. from a Render shell): flask --app app backup"""
    print(backup.create_backup())


@app.before_request
def record_visit():
    """Record storefront page views for admin analytics.

    Skipped: assets, API, health/SEO files, the admin panel, language/currency
    switch redirects and the admin's own browsing (none are customer visits).
    """
    path = request.path
    if path.startswith(("/static/", "/uploads/", "/api/", "/admin", "/set-locale/", "/set-currency/")) or path in (
        "/health", "/sitemap.xml", "/robots.txt", "/favicon.ico",
    ):
        return
    if request.method != "GET" or is_admin():
        return
    # The raw header: werkzeug's UserAgent object is falsy for real browsers
    ua = request.headers.get("User-Agent", "")
    if is_bot(ua):
        return
    try:
        # Personal data kept to a minimum: no IP, no full user agent
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        store.record_visit(path, visitor_id(client_ip(), day), store.browser_family(ua))
    except Exception:  # noqa: BLE001 - analytics never breaks a page
        app.logger.exception("Recording a visit failed")


@app.before_request
def csrf_protect():
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return None
    if not app.config.get("CSRF_ENABLED", True) or csrf_token_valid():
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": tr_msg("flash_csrf_failed")}), 400
    flash(tr_msg("flash_csrf_failed"), "error")
    return safe_back()


@app.after_request
def security_headers(response):
    if g.get("request_id"):
        response.headers.setdefault("X-Request-ID", g.request_id)
    if request.path.startswith("/static/") and response.status_code in (200, 304):
        # Versioned URLs never change content; others are revalidated daily
        response.headers["Cache-Control"] = STATIC_IMMUTABLE if request.args.get("v") else STATIC_DEFAULT
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
    )
    if response.mimetype == "text/html":
        response.headers.setdefault("Content-Security-Policy", content_security_policy())
    if os.getenv("RENDER"):
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.errorhandler(404)
def not_found(error):  # noqa: D401, ARG001
    """Handle 404 errors with a friendly marketplace page."""
    return render_template("404.html"), 404


_FALLBACK_ERROR_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>PHOENIXMARKET</title>"
    "<body style='background:#020617;color:#e2e8f0;font-family:sans-serif;text-align:center;padding:4rem'>"
    "<h1>Something went wrong</h1><p>Please try again in a moment.</p><p><a href='/' style='color:#fb923c'>Home</a></p>"
)


@app.errorhandler(500)
def server_error(error):  # noqa: ARG001
    """Unexpected errors: a friendly page, never a traceback (Flask logs the exception).

    The page shows the request id as an error code; the logged exception
    carries the same id, so a customer's report leads straight to it.
    """
    code = g.get("request_id", "")
    if request.path.startswith("/api/"):
        return jsonify({"error": tr_msg("error_heading"), "code": code}), 500
    try:
        return render_template("500.html", error_code=code), 500
    except Exception:  # noqa: BLE001 - e.g. the database itself is unavailable
        app.logger.exception("Rendering the error page failed")
        return _FALLBACK_ERROR_PAGE + f"<p>Error code: {code}</p>", 500


@app.errorhandler(413)
def request_too_large(error):  # noqa: ARG001
    """Uploads above MAX_CONTENT_LENGTH."""
    flash(tr_msg("flash_file_too_large"), "error")
    return safe_back()


if __name__ == "__main__":
    # Debug is disabled by default; enable explicitly with FLASK_DEBUG=1
    debug_flag = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_flag)
