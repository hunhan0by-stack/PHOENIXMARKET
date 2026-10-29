"""
Admin sign-in (with optional two-step sign-in) / sign-out and the dashboard.
"""
import secrets
import time

from flask import current_app, flash, redirect, render_template, request, session, url_for

import services.backup as backup
import services.legal as legal
import services.store as store
import services.totp as totp
from services.web import ADMIN_SEEN_KEY, client_ip, is_admin, logout_admin, tr_msg
from views.admin.base import admin_login_limiter, bp

# Password accepted, waiting for the authenticator code (time it was accepted)
PENDING_2FA_KEY = "admin_2fa_pending"
PENDING_2FA_SECONDS = 5 * 60
SETUP_2FA_KEY = "admin_2fa_setup"


@bp.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        limit_key = client_ip()
        wait = admin_login_limiter.retry_after(limit_key)
        if wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("admin_login.html"), 429
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        u_ok = secrets.compare_digest(
            (username or "").encode("utf-8"),
            (current_app.config["ADMIN_USERNAME"] or "").encode("utf-8"),
        )
        p_ok = secrets.compare_digest(
            (password or "").encode("utf-8"),
            (current_app.config["ADMIN_PASSWORD"] or "").encode("utf-8"),
        )
        if u_ok and p_ok:
            if totp.admin_enabled():
                # Second step: the code from the authenticator app. Failed
                # codes count against the same per-IP login limit.
                session[PENDING_2FA_KEY] = time.time()
                return redirect(url_for("admin.login_verify"))
            admin_login_limiter.reset(limit_key)
            return sign_in_admin()
        admin_login_limiter.hit(limit_key)
        flash(tr_msg("flash_admin_invalid"), "error")
    return render_template("admin_login.html")


def sign_in_admin():
    # Single-owner admin; kept separate from any customer session
    session.pop(PENDING_2FA_KEY, None)
    session["is_admin"] = True
    session["admin"] = True
    session[ADMIN_SEEN_KEY] = time.time()
    flash(tr_msg("flash_admin_login_ok"), "success")
    return redirect(url_for("admin.dashboard"))


@bp.route("/admin/login/verify", methods=["GET", "POST"])
def login_verify():
    """Second sign-in step when two-step sign-in is on: the current authenticator code."""
    started = session.get(PENDING_2FA_KEY)
    if not started or time.time() - started > PENDING_2FA_SECONDS or not totp.admin_enabled():
        session.pop(PENDING_2FA_KEY, None)
        flash(tr_msg("flash_admin_2fa_expired"), "error")
        return redirect(url_for("admin.login"))
    if request.method == "POST":
        limit_key = client_ip()
        wait = admin_login_limiter.retry_after(limit_key)
        if wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("admin_login_verify.html"), 429
        if totp.verify_admin_code(request.form.get("code", "")):
            admin_login_limiter.reset(limit_key)
            return sign_in_admin()
        admin_login_limiter.hit(limit_key)
        flash(tr_msg("flash_admin_2fa_invalid"), "error")
    return render_template("admin_login_verify.html")


@bp.route("/admin/logout", methods=["GET", "POST"])
def logout():
    # As with customer sign-out: only a CSRF-checked POST signs out
    if request.method != "POST":
        return redirect(url_for("admin.dashboard") if is_admin() else url_for("shop.index"))
    logout_admin()
    u = session.get("user") or {}
    if u.get("role") == "admin":
        session.pop("user", None)
    flash(tr_msg("flash_admin_logged_out"), "success")
    return redirect(url_for("shop.index"))


@bp.route("/admin/dashboard")
def dashboard():
    all_products = store.list_all_products()
    total_products = len(all_products)
    active_products = len([p for p in all_products if p.get("status") == "active"])
    inactive_products = total_products - active_products
    total_orders = store.count_orders()
    analytics = store.get_analytics_for_admin()
    return render_template(
        "admin_dashboard.html",
        products=all_products,
        total_products=total_products,
        active_products=active_products,
        inactive_products=inactive_products,
        total_orders=total_orders,
        total_users=store.count_users(),
        categories=store.list_categories(),
        analytics=analytics,
        # Listings sold per package are tracked per package instead
        low_stock_products=[p for p in store.list_low_stock_products() if not p["has_variants"]],
        low_stock_packages=store.list_low_stock_variants(),
        open_messages=store.count_support_messages("open"),
        sales=store.sales_report(30),
        status_counts=store.count_orders_by_status(),
        backup_overdue=backup.download_overdue(),
        legal_missing=legal.missing_seller_fields(),
        legal_reviewed=legal.texts_reviewed(),
        two_step_enabled=totp.admin_enabled(),
        storage=current_app.config.get("STORAGE_STATUS") or {},
    )


@bp.route("/admin/security")
def security():
    """Two-step sign-in: set up (QR code + first code) or turn off."""
    secret = None
    if not totp.admin_enabled():
        # The secret being set up stays in the (signed) session until a code confirms it
        secret = session.get(SETUP_2FA_KEY) or totp.new_secret()
        session[SETUP_2FA_KEY] = secret
    uri = totp.provisioning_uri(secret, current_app.config["ADMIN_USERNAME"] or "admin") if secret else None
    return render_template(
        "admin_security.html",
        enabled=secret is None,
        secret=secret,
        qr=totp.qr_svg(uri) if uri else None,
    )


@bp.route("/admin/security/enable", methods=["POST"])
def enable_two_step():
    secret = session.get(SETUP_2FA_KEY)
    if not secret or totp.admin_enabled():
        return redirect(url_for("admin.security"))
    if not totp.verify_admin_code(request.form.get("code", ""), secret=secret):
        flash(tr_msg("flash_admin_2fa_invalid"), "error")
        return redirect(url_for("admin.security"))
    totp.enable_admin(secret)
    session.pop(SETUP_2FA_KEY, None)
    flash(tr_msg("flash_admin_2fa_enabled"), "success")
    return redirect(url_for("admin.security"))


@bp.route("/admin/security/disable", methods=["POST"])
def disable_two_step():
    if totp.admin_enabled():
        if not totp.verify_admin_code(request.form.get("code", "")):
            flash(tr_msg("flash_admin_2fa_invalid"), "error")
            return redirect(url_for("admin.security"))
        totp.disable_admin()
        flash(tr_msg("flash_admin_2fa_disabled"), "info")
    return redirect(url_for("admin.security"))
