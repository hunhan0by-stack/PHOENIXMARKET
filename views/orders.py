"""
Cart, checkout, order confirmation and public order tracking.
Payment happens on itemsatis after the order is recorded.
"""
import uuid

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from services.cart import build_cart_items, get_cart
import services.coupons as coupons
from services.currency import convert, format_price
import services.notifications as notifications
from services.ratelimit import RateLimiter
import services.store as store
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
# Slows down guessing discount codes (per client IP)
coupon_limiter = RateLimiter(max_hits=20, window_seconds=10 * 60)


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
    items, subtotal_usd, subtotal_display = build_cart_items(get_cart(), currency)
    coupon, _discount_usd, discount_display = (
        session_coupon(subtotal_usd, currency) if items else (None, 0.0, 0.0)
    )
    return render_template(
        "cart.html",
        items=items,
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


@bp.route("/cart/add/<int:product_id>", methods=["POST"])
def add_to_cart(product_id):
    product = store.get_product(product_id)
    if not product or product.get("status") != "active":
        flash(tr_msg("flash_product_unavailable"), "error")
        return safe_back("shop.products")
    stock = int(product.get("stock") or 0)
    if stock <= 0:
        flash(tr_msg("flash_out_of_stock", name=product["name"]), "error")
        return safe_back("shop.products")
    cart_data = get_cart()
    key = str(product_id)
    current = cart_data.get(key, 0)
    if current + 1 > stock:
        cart_data[key] = stock
        session["cart"] = cart_data
        flash(tr_msg("flash_stock_limit", n=stock, name=product["name"]), "error")
        return safe_back("shop.products")
    cart_data[key] = current + 1
    session["cart"] = cart_data
    flash(tr_msg("flash_added_to_cart"), "success")
    return safe_back("shop.products")


@bp.route("/cart/update/<int:product_id>", methods=["POST"])
def update_cart(product_id):
    qty_raw = request.form.get("qty", "1")
    try:
        qty = max(0, int(qty_raw))
    except ValueError:
        qty = 1
    cart_data = get_cart()
    key = str(product_id)
    product = store.get_product(product_id)
    if qty == 0 or not product:
        cart_data.pop(key, None)
        session["cart"] = cart_data
        flash(tr_msg("flash_cart_updated"), "success")
        return redirect(url_for("orders.cart"))
    stock = int(product.get("stock") or 0) if product.get("status") == "active" else 0
    if qty > stock:
        qty = stock
        flash(tr_msg("flash_stock_limit", n=stock, name=product["name"]), "error")
    else:
        flash(tr_msg("flash_cart_updated"), "success")
    if qty == 0:
        cart_data.pop(key, None)
    else:
        cart_data[key] = qty
    session["cart"] = cart_data
    return redirect(url_for("orders.cart"))


@bp.route("/checkout", methods=["GET", "POST"])
def checkout():
    cart_data = get_cart()
    if not cart_data:
        flash(tr_msg("flash_cart_empty"), "error")
        return redirect(url_for("shop.products"))

    currency = get_current_currency()
    items, subtotal_base, subtotal_display = build_cart_items(cart_data, currency)
    short = next((i for i in items if i["insufficient"]), None)
    if short:
        flash(tr_msg("flash_checkout_stock", name=short["product"]["name"]), "error")
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
    }
    user = current_user()
    form = {"full_name": "", "email": user["email"] if user else "", "notes": ""}

    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        notes = request.form.get("notes", "").strip()
        form = {"full_name": full_name, "email": email, "notes": notes}

        problem = None
        if not full_name or not email:
            problem = "flash_checkout_required"
        elif not EMAIL_RE.match(email):
            problem = "flash_invalid_email"
        if problem:
            flash(tr_msg(problem), "error")
            return render_template("checkout.html", items=items, form=form, **totals)

        tracking_token = str(uuid.uuid4())
        line_items = []
        for item in items:
            p = item["product"]
            line_items.append(
                {
                    "product_id": p["id"],
                    "product_name": p["name"],
                    "quantity": item["qty"],
                    "unit_price_usd": convert(
                        get_base_price(p),
                        get_base_currency(p),
                        "USD",
                    ),
                }
            )
        try:
            order_id = store.create_order(
                tracking_token=tracking_token,
                user_email=email,
                full_name=full_name,
                notes=notes,
                total_price=round(subtotal_base - discount_usd, 2),
                total_currency="USD",
                line_items=line_items,
                user_id=user["id"] if user else None,
                locale=get_current_locale(),
                coupon=coupon,
                discount_usd=discount_usd,
            )
        except store.OutOfStockError as exc:
            flash(tr_msg("flash_checkout_stock", name=exc.product_name), "error")
            return redirect(url_for("orders.cart"))
        except store.CouponUnavailableError:
            session.pop(COUPON_SESSION_KEY, None)
            flash(tr_msg("flash_coupon_used_up"), "error")
            return redirect(url_for("orders.cart"))
        try:
            notifications.send_order_confirmation(store.get_order_by_id(order_id))
        except Exception:  # noqa: BLE001 - an email problem must never lose the order
            current_app.logger.exception("Order confirmation email failed for order %s", order_id)
        session["cart"] = {}
        session.pop(COUPON_SESSION_KEY, None)
        session["last_order_id"] = order_id
        session["last_tracking_token"] = tracking_token
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
        itemsatis_url=current_app.config["ITEMSATIS_REDIRECT_URL"],
    )


@bp.route("/track/<token>")
def track(token):
    """Public order status by secret tracking token (from confirmation email / page)."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        flash(tr_msg("flash_order_link_not_found"), "error")
        return redirect(url_for("shop.index"))
    events = store.list_order_events(order["id"], customer_view=True)
    return render_template("track_order.html", order=order, events=events)


@bp.route("/track/<token>/cancel", methods=["POST"])
def cancel(token):
    """Customers may cancel their own order while it is still pending (knowing the token)."""
    order = store.get_order_by_tracking_token(token, include_items=True)
    if not order:
        abort(404)
    if order["fulfillment_status"] != "pending":
        flash(tr_msg("flash_order_cancel_not_allowed"), "error")
        return redirect(url_for("orders.track", token=token))
    changes = store.update_order_fulfillment(
        order["id"],
        fulfillment_status="cancelled",
        tracking_number=order["tracking_number"],
        carrier_note=order["carrier_note"],
        actor="customer",
    )
    flash(tr_msg("flash_order_cancelled"), "success")
    try:
        notifications.send_order_update(store.get_order_by_id(order["id"]), changes)
        for product_id in {li["product_id"] for li in order["line_items"]}:
            notifications.notify_back_in_stock(product_id)
    except Exception:  # noqa: BLE001 - the cancellation itself is saved
        current_app.logger.exception("Emails after cancelling order %s failed", order["id"])
    return redirect(url_for("orders.track", token=token))
