"""
Listings, their packages (1K / 5K / 10K …) and categories.
"""
import math

from flask import current_app, flash, redirect, render_template, request, url_for

import services.order_fields as order_fields
import services.store as store
from services.store import unique_slug
from services.uploads import InvalidImageError, process_listing_image
from services.web import SUPPORTED_CURRENCIES, SUPPORTED_LOCALES, is_http_url, tr_msg
from views.admin.base import bp, LISTING_STATUS_CHOICES, notify_back_in_stock_safely


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


PAYMENT_URL_MAX = 500
DELIVERY_HOURS_MAX = 30 * 24


def clean_payment_url(raw):
    """(url or None, error_key or None) for an optional itemsatis link field."""
    url = (raw or "").strip()
    if not url:
        return None, None
    if not is_http_url(url, PAYMENT_URL_MAX):
        return None, "flash_payment_url_invalid"
    return url, None


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
        "payment_url": (f.get("payment_url") or "").strip(),
        "delivery_hours": (f.get("delivery_hours") or "").strip(),
    }
    data["order_fields"], fields_problem = order_fields.parse_admin_form(f)
    if not data["name"] or not data["description"] or not data["base_price"]:
        return data, "flash_listing_required"
    if fields_problem:
        return data, fields_problem
    if data["delivery_hours"]:
        try:
            data["delivery_hours"] = int(data["delivery_hours"])
        except ValueError:
            return data, "flash_delivery_hours_invalid"
        # At most 30 days: the distance sales agreement promises delivery within 30 days
        if not 1 <= data["delivery_hours"] <= DELIVERY_HOURS_MAX:
            return data, "flash_delivery_hours_invalid"
    else:
        data["delivery_hours"] = None
    data["payment_url"], problem = clean_payment_url(data["payment_url"])
    if problem:
        data["payment_url"] = (f.get("payment_url") or "").strip()  # keep the input for correction
        return data, problem
    try:
        price = float(data["base_price"])
    except ValueError:
        return data, "flash_listing_price_invalid"
    if not math.isfinite(price) or price < 0:
        return data, "flash_listing_price_invalid"
    data["base_price"] = price
    return data, None


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


def editable_listing(product: dict) -> dict:
    """
    The listing as the edit form should show it: for a listing sold per
    package, its own price/stock columns (not the package summary).
    """
    if product.get("has_variants"):
        return {**product, "base_price": product["own_price"], "stock": product["own_stock"]}
    return product


def render_listing_form(product: dict, **extra):
    return render_template(
        "admin_listing_form.html",
        mode="edit",
        product=product,
        packages=store.list_variants(product["id"]),
        categories=store.list_categories(),
        **extra,
    )


def notify_if_back_in_stock(product_id: int, stock_before: int) -> None:
    """Send back-in-stock emails when a listing just became buyable again."""
    after = store.get_product(product_id)
    if after and stock_before <= 0 < (after.get("stock") or 0):
        notify_back_in_stock_safely(product_id)


@bp.route("/admin/edit-listing/<int:product_id>", methods=["GET", "POST"])
@bp.route("/admin/listings/edit/<int:product_id>", methods=["GET", "POST"])
@bp.route("/admin/listings/<int:product_id>/edit", methods=["GET", "POST"])
def edit_listing(product_id):
    product = store.get_product(product_id)
    if not product:
        flash(tr_msg("flash_listing_not_found"), "error")
        return redirect(url_for("admin.listings"))
    editable = editable_listing(product)

    if request.method == "POST":
        data, problem = parse_listing_form(editable)
        new_image_path = None
        if not problem:
            new_image_path, problem = save_listing_image_or_error()
        if problem:
            flash(tr_msg(problem), "error")
            return render_listing_form({**editable, **data})
        if new_image_path:
            data["image_path"] = new_image_path
        if data["name"] != product.get("name"):
            data["slug"] = unique_slug(
                data["name"], store.list_existing_slugs(product_id)
            )
        store.update_product(product_id, data)
        flash(tr_msg("flash_listing_updated"), "success")
        notify_if_back_in_stock(product_id, product.get("stock") or 0)
        return redirect(url_for("admin.listings"))

    return render_listing_form(editable)


PACKAGE_NAME_MAX = 80
PACKAGE_MAX_PRICE = 1_000_000
PACKAGE_MAX_STOCK = 1_000_000


def parse_package_form(product_id: int, variant_id: int | None = None):
    """(values or None, error_key or None) for the add/edit package forms."""
    f = request.form
    name = " ".join((f.get("name") or "").split())
    if not name or len(name) > PACKAGE_NAME_MAX:
        return None, "flash_package_name_invalid"
    try:
        price = float((f.get("price") or "").strip().replace(",", "."))
        stock = int((f.get("stock") or "0").strip() or 0)
        sort_order = int((f.get("sort_order") or "0").strip() or 0)
    except ValueError:
        return None, "flash_package_values_invalid"
    if (
        not math.isfinite(price) or not 0 < price <= PACKAGE_MAX_PRICE
        or not 0 <= stock <= PACKAGE_MAX_STOCK
        or not -1000 <= sort_order <= 1000
    ):
        return None, "flash_package_values_invalid"
    if any(
        v["name"].casefold() == name.casefold() and v["id"] != variant_id
        for v in store.list_variants(product_id)
    ):
        return None, "flash_package_name_taken"
    payment_url, problem = clean_payment_url(f.get("payment_url"))
    if problem:
        return None, problem
    return {
        "name": name,
        "price": round(price, 2),
        "stock": stock,
        "sort_order": sort_order,
        "active": bool(f.get("active")),
        "payment_url": payment_url,
    }, None


def listing_package(product_id: int, variant_id: int):
    """(product, package) when the package belongs to the listing, else (None, None)."""
    product = store.get_product(product_id)
    variant = store.get_variant(variant_id)
    if not product or not variant or variant["product_id"] != product_id:
        return None, None
    return product, variant


def packages_url(product_id: int) -> str:
    return url_for("admin.edit_listing", product_id=product_id) + "#packages"


@bp.route("/admin/listings/<int:product_id>/packages", methods=["POST"])
def add_package(product_id):
    product = store.get_product(product_id)
    if not product:
        flash(tr_msg("flash_listing_not_found"), "error")
        return redirect(url_for("admin.listings"))
    data, problem = parse_package_form(product_id)
    if problem:
        flash(tr_msg(problem), "error")
        return redirect(packages_url(product_id))
    store.create_variant(product_id, **data)
    flash(tr_msg("flash_package_saved"), "success")
    notify_if_back_in_stock(product_id, product.get("stock") or 0)
    return redirect(packages_url(product_id))


@bp.route("/admin/listings/<int:product_id>/packages/<int:variant_id>", methods=["POST"])
def update_package(product_id, variant_id):
    product, variant = listing_package(product_id, variant_id)
    if not product:
        flash(tr_msg("flash_package_not_found"), "error")
        return redirect(packages_url(product_id))
    data, problem = parse_package_form(product_id, variant_id)
    if problem:
        flash(tr_msg(problem), "error")
        return redirect(packages_url(product_id))
    store.update_variant(variant_id, **data)
    flash(tr_msg("flash_package_saved"), "success")
    notify_if_back_in_stock(product_id, product.get("stock") or 0)
    return redirect(packages_url(product_id))


@bp.route("/admin/listings/<int:product_id>/packages/<int:variant_id>/delete", methods=["POST"])
def delete_package(product_id, variant_id):
    product, variant = listing_package(product_id, variant_id)
    if not product:
        flash(tr_msg("flash_package_not_found"), "error")
        return redirect(packages_url(product_id))
    # Past orders keep the package name; only the purchasable option goes away
    store.delete_variant(variant_id)
    flash(tr_msg("flash_package_deleted"), "info")
    notify_if_back_in_stock(product_id, product.get("stock") or 0)
    return redirect(packages_url(product_id))


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
