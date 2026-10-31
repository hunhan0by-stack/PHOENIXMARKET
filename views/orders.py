"""
Cart, checkout, order confirmation and public order tracking.
Payment happens on itemsatis after the order is recorded.
"""
import hashlib
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from services.cart import (
    MAX_LINE_QUANTITY,
    CartLineError,
    available_stock,
    build_cart_items,
    cart_key,
    get_cart,
    line_name,
    prune_cart,
    purchasable_line,
    resolve_cart_key,
    save_cart,
)
import services.coupons as coupons
import services.legal as legal
from services.currency import convert, format_price
from services.money import money
import services.notifications as notifications
import services.order_fields as order_fields
import services.payments as payments
from services.ratelimit import RateLimiter
import services.store as store
import services.unpaid_orders as unpaid_orders
from services.web import (
    EMAIL_RE,
    client_ip,
    current_user,
    get_current_currency,
    get_current_locale,
    safe_back,
    tr_msg,
)

bp = Blueprint("orders", __name__)

COUPON_SESSION_KEY = "coupon"
CHECKOUT_MAX_LENGTHS = {"full_name": 100, "email": 254, "notes": 2000}
# Slows down guessing discount codes (per client IP)
coupon_limiter = RateLimiter(max_hits=20, window_seconds=10 * 60)
# Every order reserves stock until it is paid or expires and emails the
# address given: cap orders per client IP, and unpaid orders per address, so
# a script can neither hoard the stock nor use checkout to flood a mailbox.
checkout_limiter = RateLimiter(max_hits=10, window_seconds=60 * 60)
MAX_OPEN_ORDERS_PER_EMAIL = 3


def coupon_error_message(err: coupons.CouponError, currency: str) -> str:
    min_usd = err.params.get("min_usd", 0)
    return tr_msg(err.key, amount=format_price(convert(min_usd, "USD", currency), currency))


def session_coupon(subtotal_usd: float, currency: str):
    """
    (coupon, discount_usd, discount_display) for the code saved in the session.
    A code that no longer applies is dropped with a flash explaining why.
    """
    code = session.get(COUPON_SESSION_KEY)
    if not code:
        return None, 0.0, 0.0
    try:
        coupon, discount_usd = coupons.evaluate(code, subtotal_usd)
    except coupons.CouponError as err:
        session.pop(COUPON_SESSION_KEY, None)
        flash(coupon_error_message(err, currency), "error")
        return None, 0.0, 0.0
    return coupon, discount_usd, convert(discount_usd, "USD", currency)


@bp.route("/cart")
def cart():
    currency = get_current_currency()
    cart_data = get_cart()
    items, subtotal_usd, subtotal_display = build_cart_items(cart_data, currency)
    prune_cart(cart_data, items)
    coupon, _discount_usd, discount_display = (
        session_coupon(subtotal_usd, currency) if items else (None, 0.0, 0.0)
    )
    return render_template(
        "cart.html",
        items=items,
        sales_closed=bool(legal.checkout_blockers()),
        subtotal=subtotal_display,
        coupon=coupon,
        discount=discount_display,
        total=max(0.0, subtotal_display - discount_display),
    )


@bp.route("/cart/coupon", methods=["POST"])
def apply_coupon():
    code = coupons.normalize_code(request.form.get("code"))
    limit_key = client_ip()
    if coupon_limiter.retry_after(limit_key):
        flash(tr_msg("flash_too_many_attempts", minutes=10), "error")
        return redirect(url_for("orders.cart"))
    coupon_limiter.hit(limit_key)
    currency = get_current_currency()
    _items, subtotal_usd, _display = build_cart_items(get_cart(), currency)
    try:
        coupons.evaluate(code, subtotal_usd)
    except coupons.CouponError as err:
        flash(coupon_error_message(err, currency), "error")
        return redirect(url_for("orders.cart"))
    session[COUPON_SESSION_KEY] = code
    flash(tr_msg("flash_coupon_applied"), "success")
    return redirect(url_for("orders.cart"))


@bp.route("/cart/coupon/remove", methods=["POST"])
def remove_coupon():
    if session.pop(COUPON_SESSION_KEY, None):
        flash(tr_msg("flash_coupon_removed"), "info")
    return redirect(url_for("orders.cart"))


def checkout_quote(items: list, discount_usd: float) -> str:
    """
    Fingerprint of what the checkout page showed (lines, quantities, unit
    prices, discount). The form echoes it back so an order is never placed at
    prices the customer has not seen.
    """
    basis = ";".join(f"{i['key']}x{i['qty']}@{i['unit_price_usd']:.2f}" for i in items)
    return hashlib.sha256(f"{basis}|{discount_usd:.2f}".encode()).hexdigest()[:20]


def requested_quantity(raw, default: int) -> int:
    """Quantity from a form field, clamped to 0..MAX_LINE_QUANTITY."""
    try:
        qty = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(0, min(qty, MAX_LINE_QUANTITY))


@bp.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    try:
        product, variant, stock = purchasable_line(product_id, request.form.get("variant_id"))
    except CartLineError as err:
        flash(tr_msg(err.key, **err.params), "error")
        if err.key in ("flash_choose_package", "flash_package_unavailable"):
            # The package picker lives on the product page
            listing = store.get_product(product_id)
            if listing and listing.get("status") == "active":
                return redirect(url_for("shop.product_detail", slug_or_id=listing["slug"]))
        return safe_back("shop.products")
    qty = max(1, requested_quantity(request.form.get("qty"), 1))
    cart_data = get_cart()
    key = cart_key(product["id"], variant["id"] if variant else None)
    current = int(cart_data.get(key, 0))
    if current + qty > stock:
        cart_data[key] = stock
        save_cart(cart_data)
        flash(tr_msg("flash_stock_limit", n=stock, name=line_name(product, variant)), "error")
        return safe_back("shop.products")
    cart_data[key] = current + qty
    save_cart(cart_data)
    flash(tr_msg("flash_added_to_cart"), "success")
    return safe_back("shop.products")


@bp.route("/cart/update/<key>", methods=["POST"])
def update_cart(key):
    """Set the quantity of one cart line ("12" or "12:3"); 0 removes it."""
    qty = requested_quantity(request.form.get("qty", "1"), 1)
    cart_data = get_cart()
    product, variant = resolve_cart_key(key)
    if qty == 0 or not product:
        cart_data.pop(key, None)
        save_cart(cart_data)
        flash(tr_msg("flash_cart_updated"), "success")
        return redirect(url_for("orders.cart"))
    key = cart_key(product["id"], variant["id"] if variant else None)
    stock = available_stock(product, variant)
    if qty > stock:
        qty = stock
        flash(tr_msg("flash_stock_limit", n=stock, name=line_name(product, variant)), "error")
    else:
        flash(tr_msg("flash_cart_updated"), "success")
    if qty == 0:
        cart_data.pop(key, None)
    else:
        cart_data[key] = qty
    save_cart(cart_data)
    return redirect(url_for("orders.cart"))


@bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart_data = get_cart()
    if not cart_data:
        flash(tr_msg("flash_cart_empty"), "error")
        return redirect(url_for("shop.products"))

    currency = get_current_currency()
    items, subtotal_base, subtotal_display = build_cart_items(cart_data, currency)
    prune_cart(cart_data, items)
    if not items:
        flash(tr_msg("flash_cart_empty"), "error")
        return redirect(url_for("shop.products"))
    short = next((i for i in items if i["insufficient"]), None)
    if short:
        flash(tr_msg("flash_checkout_stock", name=short["name"]), "error")
        return redirect(url_for("orders.cart"))

    had_coupon = bool(session.get(COUPON_SESSION_KEY))
    coupon, discount_usd, discount_display = session_coupon(subtotal_base, currency)
    if had_coupon and not coupon and request.method == "POST":
        # The code stopped applying after the customer saw the discounted total;
        # never place the order at a different price than the one shown.
        return redirect(url_for("orders.cart"))
    totals = {
        "subtotal": subtotal_display,
        "coupon": coupon,
        "discount": discount_display,
        "total": max(0.0, subtotal_display - discount_display),
        "quote": checkout_quote(items, discount_usd),
        "max_lengths": CHECKOUT_MAX_LENGTHS,
    }
    user = current_user()
    form = {"full_name": "", "email": user["email"] if user else "", "notes": ""}
    order_fields.attach_to_items(items, request.form if request.method == "POST" else None)

    # Sales are closed until the seller details are complete and the current
    # legal texts were reviewed: no order, stock, coupon use or email. Checked
    # here, on the server, before anything of the posted form is used.
    blockers = legal.checkout_blockers()
    if blockers:
        if request.method == "POST":
            current_app.logger.warning("Checkout refused, sales closed: %s", ", ".join(blockers))
        return render_template(
            "checkout.html", items=items, form=form, sales_closed=True, **totals
        ), (503 if request.method == "POST" else 200)

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()
        form = {"full_name": full_name, "email": email, "notes": notes}

        problem = None
        field_problem, params = order_fields.checkout_problem(items)
        if not full_name or not email:
            problem = "flash_checkout_required"
        elif not EMAIL_RE.match(email):
            problem = "flash_invalid_email"
        elif any(len(form[k]) > n for k, n in CHECKOUT_MAX_LENGTHS.items()):
            problem = "flash_checkout_too_long"
        elif field_problem:
            problem = field_problem
        elif not request.form.get("accept_terms"):
            # Distance sales agreement + immediate performance (no withdrawal
            # for services started at the customer's request): explicit consent
            problem = "flash_checkout_terms_required"
        elif request.form.get("quote") not in (None, totals["quote"]):
            # A price, package or discount changed since the page was shown
            problem = "flash_checkout_prices_changed"
        if problem:
            flash(tr_msg(problem, **params), "error")
            return render_template("checkout.html", items=items, form=form, **totals)
        limit_key = client_ip()
        wait = checkout_limiter.retry_after(limit_key)
        if wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("checkout.html", items=items, form=form, **totals), 429
        if store.count_open_unpaid_orders(email) >= MAX_OPEN_ORDERS_PER_EMAIL:
            flash(tr_msg("flash_checkout_open_orders", n=MAX_OPEN_ORDERS_PER_EMAIL), "error")
            return render_template("checkout.html", items=items, form=form, **totals), 429

        tracking_token = str(uuid.uuid4())
        # Every price here was just recomputed from the database
        line_items = [
            {
                "product_id": item["product"]["id"],
                "product_name": item["product"]["name"],
                "variant_id": item["variant"]["id"] if item["variant"] else None,
                "variant_name": item["variant"]["name"] if item["variant"] else None,
                "quantity": item["qty"],
                "unit_price_usd": item["unit_price_usd"],
                "customer_inputs": order_fields.answers(item),
            }
            for item in items
        ]
        try:
            order_id = store.create_order(
                tracking_token=tracking_token,
                user_email=email,
                full_name=full_name,
                notes=notes,
                total_price=money(subtotal_base - discount_usd),
                total_currency="USD",
                line_items=line_items,
                user_id=user["id"] if user else None,
                locale=get_current_locale(),
                coupon=coupon,
                discount_usd=discount_usd,
                payment_window_hours=unpaid_orders.payment_window_hours(),
                terms_version=legal.LEGAL_VERSION,
            )
        except store.OutOfStockError as exc:
            flash(tr_msg("flash_checkout_stock", name=exc.product_name), "error")
            return redirect(url_for("orders.cart"))
        except store.CouponUnavailableError:
            session.pop(COUPON_SESSION_KEY, None)
            flash(tr_msg("flash_coupon_used_up"), "error")
            return redirect(url_for("orders.cart"))
        checkout_limiter.hit(limit_key)
        placed = store.get_order_by_id(order_id)
        try:
            notifications.send_order_confirmation(placed)
        except Exception:  # noqa: BLE001 - an email problem must never lose the order
            current_app.logger.exception("Order confirmation email failed for order %s", order_id)
        try:
            notifications.send_new_order_owner(placed)
        except Exception:  # noqa: BLE001
            current_app.logger.exception("New order notification failed for order %s", order_id)
        session["cart"] = {}
        session.pop(COUPON_SESSION_KEY, None)
        session["last_order_id"] = order_id
        return redirect(url_for("orders.confirmation", token=tracking_token))

    return render_template("checkout.html", items=items, form=form, **totals)


@bp.route("/checkout/complete/<token>")
def confirmation(token):
    """Post-checkout page: tracking link and redirect to external payment."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        abort(404)
    return render_template(
        "order_confirmation.html",
        order=order,
        payment_links=payments.payment_links(order),
    )


@bp.route("/track/<token>")
def track(token):
    """Public order status by secret tracking token (from confirmation email / page)."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        flash(tr_msg("flash_order_link_not_found"), "error")
        return redirect(url_for("shop.index"))
    events = store.list_order_events(order["id"], customer_view=True)
    awaiting_payment = order["fulfillment_status"] == "pending" and not order["paid_at"]
    response = make_response(render_template(
        "track_order.html",
        order=order,
        events=events,
        deliveries=store.list_deliveries(order["id"]),
        payment_links=payments.payment_links(order) if awaiting_payment else [],
    ))
    # The page can show delivered account details: never cached or indexed
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@bp.route("/track/<token>/cancel", methods=["POST"])
def cancel(token):
    """Customers may cancel their own order while it is still pending (knowing the token)."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        abort(404)
    # Once details were handed over (e.g. delivered before the payment was
    # confirmed) the customer cannot take back the order and keep them
    if order["fulfillment_status"] != "pending" or store.list_deliveries(order["id"]):
        flash(tr_msg("flash_order_cancel_not_allowed"), "error")
        return redirect(url_for("orders.track", token=token))
    try:
        changes = store.update_order_fulfillment(
            order["id"],
            fulfillment_status="cancelled",
            tracking_number=order["tracking_number"],
            carrier_note=order["carrier_note"],
            actor="customer",
            expected_status="pending",
        )
    except store.OrderChangedError:
        # Someone (or a double click) changed the order first
        flash(tr_msg("flash_order_cancel_not_allowed"), "error")
        return redirect(url_for("orders.track", token=token))
    flash(tr_msg("flash_order_cancelled"), "success")
    try:
        notifications.send_order_update(store.get_order_by_id(order["id"]), changes)
        for product_id in {li["product_id"] for li in order["line_items"]}:
            notifications.notify_back_in_stock(product_id)
    except Exception:  # noqa: BLE001 - the cancellation itself is saved
        current_app.logger.exception("Emails after cancelling order %s failed", order["id"])
    return redirect(url_for("orders.track", token=token))
