"""
Admin panel: login, dashboard, listings, categories, site content/settings and
order fulfillment. Every route except login/logout requires the admin session.
"""
import csv
import io
import math
import secrets
from datetime import datetime, timezone

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, session, url_for

import services.coupons as coupons
from services.mailer import smtp_settings
import services.notifications as notifications
from services.pagination import Page
from services.ratelimit import RateLimiter
import services.store as store
from services.store import unique_slug
from services.uploads import InvalidImageError, process_listing_image
from services.web import (
    SUPPORTED_CURRENCIES,
    SUPPORTED_LOCALES,
    client_ip,
    is_admin,
    safe_next,
    tr_msg,
)

bp = Blueprint("admin", __name__)

ORDER_STATUS_CHOICES = ("pending", "processing", "completed", "cancelled")
LISTING_STATUS_CHOICES = ("active", "inactive")
PUBLIC_ENDPOINTS = {"admin.login", "admin.logout"}

# Brute-force protection for the admin login (per client IP)
admin_login_limiter = RateLimiter(max_hits=5, window_seconds=15 * 60)


@bp.before_request
def require_admin():
    if request.endpoint not in PUBLIC_ENDPOINTS and not is_admin():
        return redirect(url_for("admin.login"))
    return None


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


@bp.route("/admin/logout")
def logout():
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
        low_stock_products=store.list_low_stock_products(),
        open_messages=store.count_support_messages("open"),
        sales=store.sales_report(30),
        status_counts=store.count_orders_by_status(),
    )


# -----------------------------------------------------------------------------
# Listings
# -----------------------------------------------------------------------------
@bp.route("/admin/listings")
def listings():
    # Basic search and filtering for marketplace-style management
    q = (request.args.get("q") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()
    category = (request.args.get("category") or "").strip().lower()
    lang = (request.args.get("lang") or "").strip().lower()

    filtered = store.list_all_products()
    if q:
        filtered = [
            p
            for p in filtered
            if q in p.get("name", "").lower()
            or q in p.get("description", "").lower()
        ]
    if status:
        filtered = [
            p
            for p in filtered
            if p.get("status", "").lower() == status
        ]
    if category:
        filtered = [
            p
            for p in filtered
            if p.get("category", "").lower() == category
        ]
    if lang:
        filtered = [
            p
            for p in filtered
            if (p.get("listing_language") or "en").lower() == lang
        ]

    categories = sorted(
        {p.get("category", "") for p in store.list_all_products() if p.get("category")}
    )

    return render_template(
        "admin_listings.html",
        products=filtered,
        stock_alerts=store.count_pending_stock_alerts(),
        categories=categories,
        current_q=q,
        current_status=status,
        current_category=category,
        current_lang=lang,
    )


def parse_listing_form(existing=None):
    """
    Read the admin listing form. Returns (data, error_key); data always holds
    the submitted values so the form can be re-rendered without losing input.
    """
    f = request.form
    base_currency = (f.get("base_currency") or "USD").strip().upper()
    if base_currency not in SUPPORTED_CURRENCIES:
        base_currency = "USD"
    listing_lang = (f.get("listing_language") or "en").strip().lower()
    if listing_lang not in SUPPORTED_LOCALES:
        listing_lang = "en"
    status = (f.get("status") or "").strip().lower()
    if status not in LISTING_STATUS_CHOICES:
        status = existing["status"] if existing else "active"
    default_stock = existing.get("stock", 0) if existing else 0
    stock_raw = (f.get("stock") or "").strip()
    try:
        stock = max(0, int(stock_raw)) if stock_raw else default_stock
    except ValueError:
        stock = default_stock
    data = {
        "name": (f.get("name") or "").strip(),
        "description": (f.get("description") or "").strip(),
        "base_price": (f.get("base_price") or "").strip(),
        "base_currency": base_currency,
        "listing_language": listing_lang,
        "category": (f.get("category") or "").strip()
        or (existing["category"] if existing else "General"),
        "stock": stock,
        "status": status,
    }
    if not data["name"] or not data["description"] or not data["base_price"]:
        return data, "flash_listing_required"
    try:
        price = float(data["base_price"])
    except ValueError:
        return data, "flash_listing_price_invalid"
    if not math.isfinite(price) or price < 0:
        return data, "flash_listing_price_invalid"
    data["base_price"] = price
    return data, None


def notify_back_in_stock_safely(product_id: int) -> None:
    """Send back-in-stock emails; a mail problem must not break the admin action."""
    try:
        sent = notifications.notify_back_in_stock(product_id)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Back-in-stock emails failed for product %s", product_id)
        return
    if sent:
        flash(tr_msg("flash_stock_alerts_sent", n=sent), "info")


def save_listing_image_or_error():
    """Process the uploaded listing image: (path or None, error_key or None)."""
    try:
        return process_listing_image(request.files.get("image_file")), None
    except InvalidImageError:
        return None, "flash_invalid_image"


# The first (bottom-most) route is canonical for url_for; the others are
# older URLs kept working.
@bp.route("/admin/create-listing", methods=["GET", "POST"])
@bp.route("/admin/listings/new", methods=["GET", "POST"])
def new_listing():
    cat_rows = store.list_categories()

    if request.method == "POST":
        data, problem = parse_listing_form()
        image_path = None
        if not problem:
            image_path, problem = save_listing_image_or_error()
        if problem:
            flash(tr_msg(problem), "error")
            return render_template(
                "admin_listing_form.html",
                mode="new",
                product=data,
                categories=cat_rows,
            )
        data["image_path"] = image_path or ""
        data["slug"] = unique_slug(data["name"], store.list_existing_slugs())
        data["owner"] = current_app.config["ADMIN_USERNAME"] or "admin"
        store.create_product(data)
        flash(tr_msg("flash_listing_created"), "success")
        return redirect(url_for("admin.listings"))

    return render_template(
        "admin_listing_form.html",
        mode="new",
        product=None,
        categories=cat_rows,
    )


@bp.route("/admin/edit-listing/<int:product_id>", methods=["GET", "POST"])
@bp.route("/admin/listings/edit/<int:product_id>", methods=["GET", "POST"])
@bp.route("/admin/listings/<int:product_id>/edit", methods=["GET", "POST"])
def edit_listing(product_id):
    product = store.get_product(product_id)
    if not product:
        flash(tr_msg("flash_listing_not_found"), "error")
        return redirect(url_for("admin.listings"))

    cat_rows = store.list_categories()

    if request.method == "POST":
        data, problem = parse_listing_form(product)
        new_image_path = None
        if not problem:
            new_image_path, problem = save_listing_image_or_error()
        if problem:
            flash(tr_msg(problem), "error")
            return render_template(
                "admin_listing_form.html",
                mode="edit",
                product={**product, **data},
                categories=cat_rows,
            )
        if new_image_path:
            data["image_path"] = new_image_path
        if data["name"] != product.get("name"):
            data["slug"] = unique_slug(
                data["name"], store.list_existing_slugs(product_id)
            )
        store.update_product(product_id, data)
        flash(tr_msg("flash_listing_updated"), "success")
        if (product.get("stock") or 0) <= 0 < data["stock"]:
            notify_back_in_stock_safely(product_id)
        return redirect(url_for("admin.listings"))

    return render_template(
        "admin_listing_form.html",
        mode="edit",
        product=product,
        categories=cat_rows,
    )


@bp.route("/admin/delete-listing/<int:product_id>", methods=["POST"])
@bp.route("/admin/listings/<int:product_id>/delete", methods=["POST"])
def delete_listing(product_id):
    product = store.get_product(product_id)
    if not product:
        flash(tr_msg("flash_listing_not_found"), "error")
        return redirect(url_for("admin.listings"))

    store.delete_product(product_id)
    flash(tr_msg("flash_listing_deleted"), "info")
    return redirect(url_for("admin.listings"))


# -----------------------------------------------------------------------------
# Categories
# -----------------------------------------------------------------------------
@bp.route("/admin/categories", methods=["GET"])
def categories():
    return render_template(
        "admin_categories.html", categories=store.list_categories()
    )


@bp.route("/admin/create-category", methods=["POST"])
def create_category():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash(tr_msg("flash_category_name_required"), "error")
        return redirect(url_for("admin.categories"))
    cats = store.list_categories()
    if any(c["name"].lower() == name.lower() for c in cats):
        flash(tr_msg("flash_category_exists"), "error")
        return redirect(url_for("admin.categories"))
    slug = unique_slug(name, store.category_slugs_excluding())
    store.create_category(name, slug, None)
    flash(tr_msg("flash_category_created"), "success")
    return redirect(url_for("admin.categories"))


@bp.route("/admin/delete-category/<int:category_id>", methods=["POST"])
def delete_category(category_id):
    category = store.get_category(category_id)
    if not category:
        flash(tr_msg("flash_category_not_found"), "error")
        return redirect(url_for("admin.categories"))
    store.delete_category(category_id)
    flash(tr_msg("flash_category_deleted"), "info")
    return redirect(url_for("admin.categories"))


@bp.route("/admin/edit-category/<int:category_id>", methods=["GET", "POST"])
def edit_category(category_id):
    """Edit an existing category name and optional parent."""
    category = store.get_category(category_id)
    if not category:
        flash(tr_msg("flash_category_not_found"), "error")
        return redirect(url_for("admin.categories"))

    all_cats = store.list_categories()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        parent_id_raw = (request.form.get("parent_id") or "").strip()
        if not name:
            flash(tr_msg("flash_category_name_required"), "error")
            return redirect(url_for("admin.edit_category", category_id=category_id))
        if any(
            c["name"].lower() == name.lower() and c["id"] != category_id
            for c in all_cats
        ):
            flash(tr_msg("flash_category_exists"), "error")
            return redirect(url_for("admin.edit_category", category_id=category_id))

        parent_id = None
        if parent_id_raw:
            try:
                parent_id_val = int(parent_id_raw)
            except ValueError:
                parent_id_val = None
            if parent_id_val and any(c["id"] == parent_id_val for c in all_cats):
                parent_id = parent_id_val

        slug = unique_slug(name, store.category_slugs_excluding(category_id))
        store.update_category(category_id, name, slug, parent_id)
        flash(tr_msg("flash_category_updated"), "success")
        return redirect(url_for("admin.categories"))

    return render_template(
        "admin_category_edit.html",
        category=category,
        categories=all_cats,
    )


# -----------------------------------------------------------------------------
# Site content and settings
# -----------------------------------------------------------------------------
@bp.route("/admin/content", methods=["GET", "POST"])
def content():
    if request.method == "POST":
        updates = {}
        for key in store.SITE_CONTENT_KEYS:
            if key in request.form:
                updates[key] = request.form.get(key, "").strip()
        if updates:
            store.save_site_content(updates)
        flash(tr_msg("flash_content_updated"), "success")
        return redirect(url_for("admin.content"))
    return render_template("admin_content.html")


@bp.route("/admin/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        itemsatis_url = (request.form.get("itemsatis_redirect_url") or "").strip()
        support_email = (request.form.get("support_email") or "").strip()
        support_discord = (request.form.get("support_discord") or "").strip()
        if itemsatis_url and not itemsatis_url.startswith(("http://", "https://")):
            flash(tr_msg("flash_invalid_url"), "error")
            return redirect(url_for("admin.settings"))

        cur = store.load_site_settings(
            current_app.config["ITEMSATIS_REDIRECT_URL"],
            "contact@phoenixmarket.example",
            "YourDiscordTag#0000",
        )
        merged_email = support_email or cur.get("support_email", "")
        merged_discord = support_discord or cur.get("support_discord", "")
        merged_itemsatis = itemsatis_url or cur.get("itemsatis_redirect_url", "")

        store.save_site_settings(merged_itemsatis, merged_email, merged_discord)
        if merged_itemsatis:
            current_app.config["ITEMSATIS_REDIRECT_URL"] = merged_itemsatis

        flash(tr_msg("flash_settings_updated"), "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html")


# -----------------------------------------------------------------------------
# Orders
# -----------------------------------------------------------------------------
ORDERS_PER_PAGE = 25
CSV_EXPORT_LIMIT = 10000


def order_filters_from_request():
    """(status or None, query or None) from ?status=&q=; unknown statuses are ignored."""
    status = (request.args.get("status") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    return (status if status in ORDER_STATUS_CHOICES else None), (q or None)


@bp.route("/admin/orders")
def orders():
    status, q = order_filters_from_request()
    total = store.count_orders_filtered(status, q)
    page = Page.from_arg(request.args.get("page"), ORDERS_PER_PAGE, total)
    return render_template(
        "admin_orders.html",
        orders=store.search_orders(status, q, limit=page.per_page, offset=page.offset),
        page=page,
        current_status=status or "",
        current_q=q or "",
        filter_args={k: v for k, v in (("status", status), ("q", q)) if v},
        status_counts=store.count_orders_by_status(),
        status_options=ORDER_STATUS_CHOICES,
    )


def csv_safe(value) -> str:
    """Neutralise spreadsheet formulas in customer-provided cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.route("/admin/orders.csv")
def orders_csv():
    status, q = order_filters_from_request()
    rows = store.search_orders(status, q, limit=CSV_EXPORT_LIMIT, include_items=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "created_at", "status", "full_name", "email", "total_usd",
        "discount_usd", "coupon", "items", "tracking_number", "customer_account", "notes",
    ])
    for o in rows:
        items = "; ".join(f"{li['product_name']} x{li['quantity']}" for li in o["line_items"])
        writer.writerow([
            o["id"],
            o["created_at"].strftime("%Y-%m-%d %H:%M") if o["created_at"] else "",
            o["fulfillment_status"],
            csv_safe(o["full_name"]),
            csv_safe(o["user_email"]),
            f"{o['total_price']:.2f}",
            f"{o['discount_usd']:.2f}",
            o["coupon_code"] or "",
            csv_safe(items),
            csv_safe(o["tracking_number"]),
            "yes" if o["user_id"] else "no",
            csv_safe(o["notes"]),
        ])
    return Response(
        "﻿" + buf.getvalue(),  # BOM so Excel detects UTF-8
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=phoenixmarket-orders.csv"},
    )


@bp.route("/admin/orders/<int:order_id>", methods=["GET", "POST"])
def order_detail(order_id):
    order = store.get_order_by_id(order_id, include_items=True)
    if not order:
        flash(tr_msg("flash_order_not_found"), "error")
        return redirect(url_for("admin.orders"))
    if request.method == "POST":
        status = (request.form.get("fulfillment_status") or "").strip().lower()
        if status not in ORDER_STATUS_CHOICES:
            status = order["fulfillment_status"]
        tracking_number = (request.form.get("tracking_number") or "").strip() or None
        carrier_note = (request.form.get("carrier_note") or "").strip() or None
        try:
            changes = store.update_order_fulfillment(
                order_id,
                fulfillment_status=status,
                tracking_number=tracking_number,
                carrier_note=carrier_note,
            )
        except store.OutOfStockError as exc:
            flash(tr_msg("flash_uncancel_stock", name=exc.product_name), "error")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        flash(tr_msg("flash_order_updated"), "success")
        if status == "cancelled" and order["fulfillment_status"] != "cancelled":
            for product_id in {li["product_id"] for li in order["line_items"]}:
                notify_back_in_stock_safely(product_id)
        if changes and request.form.get("notify_customer"):
            try:
                notifications.send_order_update(store.get_order_by_id(order_id), changes)
                flash(tr_msg("flash_customer_notified"), "info")
            except Exception:  # noqa: BLE001 - the update itself is already saved
                current_app.logger.exception("Order update email failed for order %s", order_id)
        return redirect(url_for("admin.order_detail", order_id=order_id))
    return render_template(
        "admin_order_detail.html",
        order=order,
        events=store.list_order_events(order_id),
        status_options=ORDER_STATUS_CHOICES,
    )


@bp.route("/admin/orders/<int:order_id>/notes", methods=["POST"])
def add_order_note(order_id):
    if not store.get_order_by_id(order_id, include_items=False):
        flash(tr_msg("flash_order_not_found"), "error")
        return redirect(url_for("admin.orders"))
    note = (request.form.get("note") or "").strip()
    if not note:
        flash(tr_msg("flash_note_empty"), "error")
    else:
        store.add_order_internal_note(order_id, note[:2000])
        flash(tr_msg("flash_note_added"), "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


# -----------------------------------------------------------------------------
# Coupons
# -----------------------------------------------------------------------------
def _parse_day(raw: str, end_of_day: bool):
    """'YYYY-MM-DD' -> aware UTC datetime at the start (or end) of that day; '' -> None."""
    if not raw:
        return None
    day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return day.replace(hour=23, minute=59, second=59) if end_of_day else day


def coupon_form_values(coupon=None) -> dict:
    """String values for the coupon form (blank defaults for a new coupon)."""
    if not coupon:
        return {"code": "", "kind": "percent", "value": "", "min_order_usd": "0",
                "max_uses": "", "starts_at": "", "expires_at": "", "active": True}
    return {
        "code": coupon["code"],
        "kind": coupon["kind"],
        "value": f"{coupon['value']:g}",
        "min_order_usd": f"{coupon['min_order_usd']:g}",
        "max_uses": "" if coupon["max_uses"] is None else str(coupon["max_uses"]),
        "starts_at": coupon["starts_at"].strftime("%Y-%m-%d") if coupon["starts_at"] else "",
        "expires_at": coupon["expires_at"].strftime("%Y-%m-%d") if coupon["expires_at"] else "",
        "active": coupon["active"],
    }


def parse_coupon_form():
    """(values for the store or None, raw form values, error_key or None)."""
    f = request.form
    raw = {
        "code": coupons.normalize_code(f.get("code")),
        "kind": (f.get("kind") or "percent").strip().lower(),
        "value": (f.get("value") or "").strip(),
        "min_order_usd": (f.get("min_order_usd") or "").strip() or "0",
        "max_uses": (f.get("max_uses") or "").strip(),
        "starts_at": (f.get("starts_at") or "").strip(),
        "expires_at": (f.get("expires_at") or "").strip(),
        "active": bool(f.get("active")),
    }
    if not coupons.CODE_RE.match(raw["code"]):
        return None, raw, "flash_coupon_code_invalid"
    if raw["kind"] not in coupons.KINDS:
        raw["kind"] = "percent"
    try:
        value = float(raw["value"])
        min_order = float(raw["min_order_usd"])
        max_uses = int(raw["max_uses"]) if raw["max_uses"] else None
    except ValueError:
        return None, raw, "flash_coupon_value_invalid"
    if (
        not math.isfinite(value) or value <= 0
        or (raw["kind"] == "percent" and value > 100)
        or not math.isfinite(min_order) or min_order < 0
        or (max_uses is not None and max_uses < 1)
    ):
        return None, raw, "flash_coupon_value_invalid"
    try:
        starts = _parse_day(raw["starts_at"], end_of_day=False)
        expires = _parse_day(raw["expires_at"], end_of_day=True)
    except ValueError:
        return None, raw, "flash_coupon_dates_invalid"
    if starts and expires and expires <= starts:
        return None, raw, "flash_coupon_dates_invalid"
    data = {
        "code": raw["code"], "kind": raw["kind"], "value": value, "min_order_usd": min_order,
        "max_uses": max_uses, "starts_at": starts, "expires_at": expires, "active": raw["active"],
    }
    return data, raw, None


@bp.route("/admin/coupons", methods=["GET", "POST"])
def coupons_list():
    form = coupon_form_values()
    if request.method == "POST":
        data, form, problem = parse_coupon_form()
        if not problem and store.create_coupon(data) is None:
            problem = "flash_coupon_code_taken"
        if not problem:
            flash(tr_msg("flash_coupon_saved"), "success")
            return redirect(url_for("admin.coupons_list"))
        flash(tr_msg(problem), "error")
    return render_template(
        "admin_coupons.html",
        coupons=store.list_coupons(),
        form=form,
        now=datetime.now(timezone.utc),
    )


@bp.route("/admin/coupons/<int:coupon_id>/edit", methods=["GET", "POST"])
def edit_coupon(coupon_id):
    coupon = store.get_coupon(coupon_id)
    if not coupon:
        flash(tr_msg("flash_coupon_not_found"), "error")
        return redirect(url_for("admin.coupons_list"))
    form = coupon_form_values(coupon)
    if request.method == "POST":
        data, form, problem = parse_coupon_form()
        if not problem and not store.update_coupon(coupon_id, data):
            problem = "flash_coupon_code_taken"
        if not problem:
            flash(tr_msg("flash_coupon_saved"), "success")
            return redirect(url_for("admin.coupons_list"))
        flash(tr_msg(problem), "error")
    return render_template("admin_coupon_edit.html", coupon=coupon, form=form)


@bp.route("/admin/coupons/<int:coupon_id>/toggle", methods=["POST"])
def toggle_coupon(coupon_id):
    coupon = store.get_coupon(coupon_id)
    if coupon:
        store.set_coupon_active(coupon_id, not coupon["active"])
        flash(tr_msg("flash_coupon_saved"), "success")
    return redirect(url_for("admin.coupons_list"))


@bp.route("/admin/coupons/<int:coupon_id>/delete", methods=["POST"])
def delete_coupon(coupon_id):
    if store.get_coupon(coupon_id):
        store.delete_coupon(coupon_id)
        flash(tr_msg("flash_coupon_deleted"), "info")
    return redirect(url_for("admin.coupons_list"))


# -----------------------------------------------------------------------------
# Review moderation
# -----------------------------------------------------------------------------
REVIEWS_PER_PAGE = 30


@bp.route("/admin/reviews")
def reviews():
    status = (request.args.get("status") or "").strip().lower()
    status = status if status in store.REVIEW_STATUSES else None
    page = Page.from_arg(request.args.get("page"), REVIEWS_PER_PAGE, store.count_reviews(status))
    return render_template(
        "admin_reviews.html",
        reviews=store.list_reviews_admin(status, limit=page.per_page, offset=page.offset),
        page=page,
        current_status=status or "",
        filter_args={"status": status} if status else {},
        counts={s: store.count_reviews(s) for s in store.REVIEW_STATUSES},
    )


@bp.route("/admin/reviews/<int:review_id>/status", methods=["POST"])
def set_review_status(review_id):
    status = (request.form.get("status") or "").strip().lower()
    if status in store.REVIEW_STATUSES:
        store.set_review_status(review_id, status)
        flash(tr_msg("flash_review_moderated"), "success")
    return redirect(safe_next(request.form.get("next"), url_for("admin.reviews")))


@bp.route("/admin/reviews/<int:review_id>/delete", methods=["POST"])
def delete_review(review_id):
    store.delete_review(review_id)
    flash(tr_msg("flash_review_deleted"), "info")
    return redirect(safe_next(request.form.get("next"), url_for("admin.reviews")))


# -----------------------------------------------------------------------------
# Support inbox (contact form messages)
# -----------------------------------------------------------------------------
MESSAGES_PER_PAGE = 30
SUPPORT_REPLY_MAX_LENGTH = 5000


@bp.route("/admin/messages")
def messages():
    status = (request.args.get("status") or "open").strip().lower()
    status = status if status in store.SUPPORT_STATUSES or status == "all" else "open"
    filter_status = None if status == "all" else status
    page = Page.from_arg(
        request.args.get("page"), MESSAGES_PER_PAGE, store.count_support_messages(filter_status)
    )
    return render_template(
        "admin_messages.html",
        messages=store.list_support_messages(filter_status, limit=page.per_page, offset=page.offset),
        page=page,
        current_status=status,
        filter_args={"status": status},
        counts={s: store.count_support_messages(s) for s in store.SUPPORT_STATUSES},
    )


@bp.route("/admin/messages/<int:message_id>", methods=["GET", "POST"])
def message_detail(message_id):
    message = store.get_support_message(message_id)
    if not message:
        flash(tr_msg("flash_message_not_found"), "error")
        return redirect(url_for("admin.messages"))
    if request.method == "POST":
        reply = (request.form.get("reply") or "").strip()
        if not reply or len(reply) > SUPPORT_REPLY_MAX_LENGTH:
            flash(tr_msg("flash_reply_invalid"), "error")
            return redirect(url_for("admin.message_detail", message_id=message_id))
        store.save_support_reply(message_id, reply, close=bool(request.form.get("close")))
        try:
            notifications.send_support_reply(store.get_support_message(message_id))
            flash(tr_msg("flash_reply_sent"), "success")
        except Exception:  # noqa: BLE001 - the reply is stored even if email fails
            current_app.logger.exception("Support reply email failed for message %s", message_id)
        return redirect(url_for("admin.message_detail", message_id=message_id))
    return render_template("admin_message_detail.html", message=message)


@bp.route("/admin/messages/<int:message_id>/status", methods=["POST"])
def message_status(message_id):
    status = (request.form.get("status") or "").strip().lower()
    if status in store.SUPPORT_STATUSES and store.get_support_message(message_id):
        store.set_support_status(message_id, status)
    return redirect(url_for("admin.message_detail", message_id=message_id))


# -----------------------------------------------------------------------------
# Email outbox
# -----------------------------------------------------------------------------
EMAILS_PER_PAGE = 30


@bp.route("/admin/emails")
def emails():
    page = Page.from_arg(request.args.get("page"), EMAILS_PER_PAGE, store.count_emails())
    return render_template(
        "admin_emails.html",
        emails=store.list_emails(limit=page.per_page, offset=page.offset),
        page=page,
        smtp_enabled=smtp_settings() is not None,
    )


@bp.route("/admin/emails/<int:email_id>")
def email_detail(email_id):
    email = store.get_email(email_id)
    if not email:
        flash(tr_msg("flash_email_not_found"), "error")
        return redirect(url_for("admin.emails"))
    return render_template("admin_email_detail.html", email=email)
