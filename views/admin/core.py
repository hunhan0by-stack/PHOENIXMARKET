"""
Admin sign-in / sign-out and the dashboard.
"""
import secrets

from flask import current_app, flash, redirect, render_template, request, session, url_for

import services.backup as backup
import services.legal as legal
import services.store as store
from services.web import client_ip, is_admin, tr_msg
from views.admin.base import admin_login_limiter, bp


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
            # Single-owner admin; kept separate from any customer session
            admin_login_limiter.reset(limit_key)
            session["is_admin"] = True
            session["admin"] = True
            flash(tr_msg("flash_admin_login_ok"), "success")
            return redirect(url_for("admin.dashboard"))
        admin_login_limiter.hit(limit_key)
        flash(tr_msg("flash_admin_invalid"), "error")
    return render_template("admin_login.html")


@bp.route("/admin/logout", methods=["GET", "POST"])
def logout():
    # As with customer sign-out: only a CSRF-checked POST signs out
    if request.method != "POST":
        return redirect(url_for("admin.dashboard") if is_admin() else url_for("shop.index"))
    session.pop("is_admin", None)
    session.pop("admin", None)
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
    )
