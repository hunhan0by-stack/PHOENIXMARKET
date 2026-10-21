"""
Public storefront: home, catalog, product pages, search, categories, contact,
locale/currency switchers, uploads, sitemap and health check.
"""
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from markupsafe import escape

from services.currency import convert, format_price
import services.legal as legal
from services.listing import get_base_currency, get_base_price
import services.notifications as notifications
from services.pagination import Page
from services.ratelimit import RateLimiter
import services.store as store
import services.unpaid_orders as unpaid_orders
from services.uploads import product_image_url, upload_folder
from services.web import (
    CURRENCY_KEY,
    EMAIL_RE,
    LOCALE_KEY,
    SUPPORTED_CURRENCIES,
    SUPPORTED_LOCALES,
    client_ip,
    current_user,
    get_current_currency,
    get_current_locale,
    is_admin,
    safe_back,
    tr_msg,
)

bp = Blueprint("shop", __name__)


@bp.route("/")
def index():
    products = store.list_public_products()
    featured = products[:4]
    return render_template(
        "index.html",
        products=products,
        featured_products=featured,
        categories=store.list_categories(),
    )


# -----------------------------------------------------------------------------
# Catalog browsing: sort, in-stock filter and pagination shared by the
# products, search and category pages.
# -----------------------------------------------------------------------------
CATALOG_PAGE_SIZE = 12
CATALOG_SORTS = ("featured", "newest", "price_asc", "price_desc", "rating")


def normalize_text(text: str) -> str:
    """Case-insensitive form that also matches Turkish dotted/dotless I."""
    return (text or "").casefold().replace("̇", "").replace("ı", "i")


def usd_price(product: dict) -> float:
    return convert(get_base_price(product), get_base_currency(product), "USD")


def catalog_page(products: list, extra_args: dict | None = None):
    """(items on this page, Page, controls) from ?sort=&in_stock=&page=."""
    sort = request.args.get("sort", "featured")
    if sort not in CATALOG_SORTS:
        sort = "featured"
    in_stock = request.args.get("in_stock") == "1"
    if in_stock:
        products = [p for p in products if (p.get("stock") or 0) > 0]
    if sort == "newest":
        products = sorted(products, key=lambda p: p["created_at"], reverse=True)
    elif sort in ("price_asc", "price_desc"):
        products = sorted(products, key=usd_price, reverse=sort == "price_desc")
    elif sort == "rating":
        products = sorted(products, key=lambda p: (p["avg_rating"], p["review_count"]), reverse=True)
    page = Page.from_arg(request.args.get("page"), CATALOG_PAGE_SIZE, len(products))
    args = dict(extra_args or {})
    if sort != "featured":
        args["sort"] = sort
    if in_stock:
        args["in_stock"] = "1"
    controls = {"sort": sort, "in_stock": in_stock, "args": args, "sorts": CATALOG_SORTS}
    return products[page.offset:page.offset + page.per_page], page, controls


@bp.route("/products")
def products():
    items, page, controls = catalog_page(store.list_public_products())
    return render_template("products.html", products=items, page=page, controls=controls)


@bp.route("/product/<slug_or_id>")
def product_detail(slug_or_id):
    """SEO-friendly product detail page: accepts either slug or numeric ID."""
    product = None
    if slug_or_id.isdigit():
        product = store.get_product(int(slug_or_id))
    if not product:
        product = store.get_product_by_slug(slug_or_id)
    if not product:
        abort(404)
    # Only admins can see inactive/draft products directly
    if product.get("status") != "active" and not is_admin():
        abort(404)
    user = current_user()
    packages = product_packages(product) if product["has_variants"] else []
    return render_template(
        "product_detail.html",
        product=product,
        packages=packages,
        default_package_id=next((v["id"] for v in packages if v["stock"] > 0), None),
        reviews=store.list_product_reviews(product["id"]),
        user_review=store.get_user_review(user["id"], product["id"]) if user else None,
        can_review=bool(user) and store.user_can_review(user["id"], product["id"]),
        review_limits=(REVIEW_MIN_LENGTH, REVIEW_MAX_LENGTH),
        related=related_products(product),
        jsonld=product_jsonld(product),
        image_url=absolute_url(product_image_url(product)),
    )


def product_packages(product: dict) -> list:
    """Active packages with their price formatted in the visitor's currency."""
    currency = get_current_currency()
    base_currency = get_base_currency(product)
    packages = store.list_variants(product["id"], active_only=True)
    for v in packages:
        v["display_price"] = format_price(convert(v["price"], base_currency, currency), currency)
    return packages


def related_products(product: dict, limit: int = 4) -> list:
    """Same-category listings first, then the best-rated others."""
    others = [p for p in store.list_public_products() if p["id"] != product["id"]]
    category = (product.get("category") or "").lower()
    same = [p for p in others if (p.get("category") or "").lower() == category]
    rest = sorted(
        (p for p in others if p not in same),
        key=lambda p: (p["avg_rating"], p["review_count"]),
        reverse=True,
    )
    return (same + rest)[:limit]


def absolute_url(url: str | None) -> str | None:
    if url and url.startswith("/"):
        return request.url_root.rstrip("/") + url
    return url


def product_jsonld(product: dict) -> dict:
    """schema.org Product data for search engines."""
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": product["name"],
        "description": product.get("description") or "",
        "sku": str(product["id"]),
        "category": product.get("category") or "",
        "offers": {
            "@type": "Offer",
            "price": f"{get_base_price(product):.2f}",
            "priceCurrency": get_base_currency(product),
            "availability": "https://schema.org/InStock"
            if (product.get("stock") or 0) > 0
            else "https://schema.org/OutOfStock",
            "url": url_for("shop.product_detail", slug_or_id=product["slug"], _external=True),
        },
    }
    if product.get("price_from"):
        offers = data["offers"]
        offers["@type"] = "AggregateOffer"
        offers["lowPrice"] = offers.pop("price")
        offers["highPrice"] = f"{product['max_price']:.2f}"
    image = absolute_url(product_image_url(product))
    if image:
        data["image"] = image
    if product["review_count"]:
        data["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": product["avg_rating"],
            "reviewCount": product["review_count"],
        }
    return data


# -----------------------------------------------------------------------------
# Reviews (verified buyers only)
# -----------------------------------------------------------------------------
REVIEW_MIN_LENGTH = 10
REVIEW_MAX_LENGTH = 2000


def _reviewable_product(product_id):
    """(product, user) when the signed-in customer may review it, else a redirect."""
    product = store.get_product(product_id)
    if not product or product.get("status") != "active":
        abort(404)
    user = current_user()
    if not user:
        return None, None, redirect(url_for("account.signin"))
    back = redirect(url_for("shop.product_detail", slug_or_id=product["slug"]) + "#reviews")
    if not store.user_can_review(user["id"], product_id):
        flash(tr_msg("flash_review_not_allowed"), "error")
        return None, None, back
    return product, user, back


@bp.route("/product/<int:product_id>/review", methods=["POST"])
def submit_review(product_id):
    product, user, back = _reviewable_product(product_id)
    if not product:
        return back
    try:
        rating = int(request.form.get("rating", ""))
    except ValueError:
        rating = 0
    body = (request.form.get("body") or "").strip()
    if not 1 <= rating <= 5 or not REVIEW_MIN_LENGTH <= len(body) <= REVIEW_MAX_LENGTH:
        flash(tr_msg("flash_review_invalid", min=REVIEW_MIN_LENGTH, max=REVIEW_MAX_LENGTH), "error")
        return back
    store.save_review(
        user["id"], product_id, rating, body, store.reviewer_display_name(user["id"], user["email"])
    )
    flash(tr_msg("flash_review_saved"), "success")
    return back


@bp.route("/product/<int:product_id>/review/delete", methods=["POST"])
def delete_review(product_id):
    product, user, back = _reviewable_product(product_id)
    if not product:
        return back
    store.delete_user_review(user["id"], product_id)
    flash(tr_msg("flash_review_deleted"), "info")
    return back


# -----------------------------------------------------------------------------
# Favourites and back-in-stock alerts
# -----------------------------------------------------------------------------
# Per client IP, so the alert form cannot be used to spam addresses
stock_alert_limiter = RateLimiter(max_hits=10, window_seconds=60 * 60)


@bp.route("/wishlist/<int:product_id>", methods=["POST"])
def toggle_wishlist(product_id):
    user = current_user()
    if not user:
        flash(tr_msg("flash_signin_for_favorites"), "info")
        return redirect(url_for("account.signin"))
    product = store.get_product(product_id)
    if not product or product.get("status") != "active":
        abort(404)
    saved = store.toggle_wishlist(user["id"], product_id)
    flash(tr_msg("flash_favorite_added" if saved else "flash_favorite_removed"), "success")
    return safe_back("shop.product_detail", slug_or_id=product["slug"])


@bp.route("/product/<int:product_id>/notify", methods=["POST"])
def stock_alert(product_id):
    product = store.get_product(product_id)
    if not product or product.get("status") != "active":
        abort(404)
    back = redirect(url_for("shop.product_detail", slug_or_id=product["slug"]))
    email = (request.form.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        flash(tr_msg("flash_invalid_email"), "error")
        return back
    limit_key = client_ip()
    wait = stock_alert_limiter.retry_after(limit_key)
    if wait:
        flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
        return back
    stock_alert_limiter.hit(limit_key)
    store.add_stock_alert(product_id, email, get_current_locale())
    flash(tr_msg("flash_stock_alert_saved"), "success")
    return back


@bp.route("/search")
def search():
    """Marketplace search: every word must appear in the title, description or category."""
    q = (request.args.get("q") or "").strip()[:100]
    words = normalize_text(q).split()
    products = store.list_public_products()
    if words:
        products = [
            p
            for p in products
            if all(
                w in normalize_text(" ".join((p.get("name", ""), p.get("description", ""), p.get("category", ""))))
                for w in words
            )
        ]
    items, page, controls = catalog_page(products, {"q": q} if q else None)
    return render_template("search.html", products=items, query=q, page=page, controls=controls)


@bp.route("/category/<category_slug>")
def category(category_slug):
    """Public category browsing page."""
    cat = store.get_category_by_slug(category_slug)
    if not cat:
        abort(404)
    name = cat["name"]
    products = [
        p
        for p in store.list_public_products()
        if (p.get("category") or "").lower() == name.lower()
    ]
    items, page, controls = catalog_page(products, {"category_slug": category_slug})
    return render_template(
        "category.html", category=cat, products=items, page=page, controls=controls
    )


# -----------------------------------------------------------------------------
# Contact form -> admin support inbox
# -----------------------------------------------------------------------------
CONTACT_MAX_LENGTHS = {"name": 100, "email": 254, "subject": 150, "message": 5000, "order_ref": 64}
CONTACT_MIN_MESSAGE = 10
# Per client IP, counts only accepted messages
contact_limiter = RateLimiter(max_hits=5, window_seconds=60 * 60)


def _contact_form_valid(form: dict) -> bool:
    return bool(
        form["name"]
        and EMAIL_RE.match(form["email"])
        and form["subject"]
        and len(form["message"]) >= CONTACT_MIN_MESSAGE
        and all(len(form[k]) <= n for k, n in CONTACT_MAX_LENGTHS.items())
    )


def _notify_shop_owner(message_id: int) -> None:
    """Email the configured support address about a new message (skips placeholders)."""
    to_address = notifications.owner_address()
    if not to_address:
        return
    try:
        notifications.send_support_notification(store.get_support_message(message_id), to_address)
    except Exception:  # noqa: BLE001 - the message is already stored
        current_app.logger.exception("Support notification failed for message %s", message_id)


@bp.route("/contact", methods=["GET", "POST"])
def contact():
    """Contact / support page with a message form."""
    user = current_user()
    form = {"name": "", "email": user["email"] if user else "", "subject": "", "message": "", "order_ref": ""}
    if request.method == "POST":
        form = {key: (request.form.get(key) or "").strip() for key in form}
        if request.form.get("website"):
            # Honeypot field, invisible to people: pretend success to bots.
            flash(tr_msg("flash_contact_sent"), "success")
            return redirect(url_for("shop.contact"))
        limit_key = client_ip()
        wait = contact_limiter.retry_after(limit_key)
        if wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("contact.html", form=form), 429
        if not _contact_form_valid(form):
            flash(tr_msg("flash_contact_invalid", min=CONTACT_MIN_MESSAGE), "error")
            return render_template("contact.html", form=form)
        contact_limiter.hit(limit_key)
        message_id = store.create_support_message(
            name=form["name"],
            email=form["email"],
            subject=form["subject"],
            message=form["message"],
            order_ref=form["order_ref"],
            user_id=user["id"] if user else None,
            locale=get_current_locale(),
        )
        _notify_shop_owner(message_id)
        flash(tr_msg("flash_contact_sent"), "success")
        return redirect(url_for("shop.contact"))
    return render_template("contact.html", form=form)


# -----------------------------------------------------------------------------
# Locale and currency switchers
# -----------------------------------------------------------------------------
@bp.route("/set-locale/<locale>")
def set_locale(locale):
    """Set user locale and redirect back."""
    loc = (locale or "").lower()[:2]
    if loc in SUPPORTED_LOCALES:
        session[LOCALE_KEY] = loc
    return safe_back()


@bp.route("/set-currency/<currency>")
def set_currency(currency):
    """Set user currency and redirect back."""
    cur = (currency or "").upper()
    if cur in SUPPORTED_CURRENCIES:
        session[CURRENCY_KEY] = cur
    return safe_back()


@bp.route("/uploads/<path:filename>")
def uploaded_file(filename):
    """Serve listing images from UPLOAD_FOLDER (may live outside static/)."""
    return send_from_directory(upload_folder(), filename, max_age=7 * 24 * 3600)


# -----------------------------------------------------------------------------
# SEO and health check (for Render and load balancers)
# -----------------------------------------------------------------------------
@bp.route("/legal/<page>")
def legal_page(page):
    """Privacy / KVKK notice, distance sales agreement, returns and delivery, cookies."""
    if page not in legal.PAGES:
        abort(404)
    return render_template(
        "legal_page.html",
        page=page,
        seller=legal.seller_info(),
        payment_window_hours=unpaid_orders.payment_window_hours(),
        legal_version=legal.LEGAL_VERSION,
    )


@bp.route("/sitemap.xml")
def sitemap():
    """Generate sitemap for SEO."""
    base_url = request.url_root.rstrip("/")
    pages = [("/", "daily", "1.0"), ("/products", "daily", "0.9"), ("/contact", "monthly", "0.5")]
    pages += [(f"/legal/{page}", "yearly", "0.2") for page in legal.PAGES]
    for c in store.list_categories():
        pages.append((f"/category/{c['slug']}", "weekly", "0.8"))
    pages = [(path, freq, prio, None) for path, freq, prio in pages]
    for p in store.list_public_products():
        slug = p.get("slug") or str(p["id"])
        lastmod = p["updated_at"].date().isoformat() if p.get("updated_at") else None
        pages.append((f"/product/{slug}", "weekly", "0.8", lastmod))
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for path, changefreq, priority, lastmod in pages:
        last = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        xml.append(
            f"  <url><loc>{escape(base_url + path)}</loc>{last}"
            f"<changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>"
        )
    xml.append("</urlset>")
    return "\n".join(xml), 200, {"Content-Type": "application/xml"}


@bp.route("/robots.txt")
def robots_txt():
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /account",
        "Disallow: /cart",
        "Disallow: /checkout",
        "Disallow: /track/",
        "Disallow: /reset-password/",
        f"Sitemap: {request.url_root.rstrip('/')}/sitemap.xml",
    ]
    return "\n".join(lines) + "\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@bp.route("/health")
def health():
    """Lightweight health check; skip analytics."""
    return jsonify({"status": "ok"}), 200
