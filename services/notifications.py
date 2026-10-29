"""
Customer emails (order confirmation, order updates, password reset), rendered
from templates/emails/ in the customer's language, and notifications for the
shop owner (new order, new support message) in the language chosen under
Admin → Settings. All are sent via services.mailer.
"""
from flask import render_template, url_for

from services.mailer import send_email
import services.payments as payments
import services.store as store
from services.translations import t as translate
from services.web import EMAIL_RE, SUPPORTED_LOCALES

OWNER_LOCALE_KEY = "site_settings.owner_locale"


def _render(name: str, locale: str, **context):
    """Render the .txt and .html variants of templates/emails/<name>."""
    def t(key):
        return translate(key, locale)

    text = render_template(f"emails/{name}.txt", t=t, locale=locale, **context)
    html = render_template(f"emails/{name}.html", t=t, locale=locale, **context)
    return text, html


def send_order_confirmation(order: dict) -> int:
    locale = order.get("locale") or "en"
    text, html = _render(
        "order_confirmation",
        locale,
        order=order,
        track_url=url_for("orders.track", token=order["tracking_token"], _external=True),
        payment_links=payments.payment_links(order),
    )
    subject = translate("email_order_subject", locale).format(id=order["id"])
    return send_email(order["user_email"], subject, text, html, kind="order_confirmation")


def send_order_update(order: dict, changes: list) -> int:
    """changes: event types just recorded (status_changed, tracking_updated, customer_note)."""
    locale = order.get("locale") or "en"
    text, html = _render(
        "order_update",
        locale,
        order=order,
        changes=changes,
        track_url=url_for("orders.track", token=order["tracking_token"], _external=True),
    )
    subject = translate("email_update_subject", locale).format(id=order["id"])
    return send_email(order["user_email"], subject, text, html, kind="order_update")


def send_delivery_ready(order: dict) -> int:
    """The order was delivered: a link to the tracking page, never the delivered details."""
    locale = order.get("locale") or "en"
    text, html = _render(
        "delivery_ready",
        locale,
        order=order,
        track_url=url_for("orders.track", token=order["tracking_token"], _external=True),
    )
    subject = translate("email_delivery_subject", locale).format(id=order["id"])
    return send_email(order["user_email"], subject, text, html, kind="delivery_ready")


def send_password_reset(to_address: str, reset_url: str, locale: str) -> int:
    text, html = _render("password_reset", locale, reset_url=reset_url)
    subject = translate("email_reset_subject", locale)
    return send_email(to_address, subject, text, html, kind="password_reset")


def notify_back_in_stock(product_id: int) -> int:
    """Email everyone waiting for a listing once it can be bought again; returns emails sent."""
    product = store.get_product(product_id)
    if not product or product.get("status") != "active" or (product.get("stock") or 0) <= 0:
        return 0
    product_url = url_for("shop.product_detail", slug_or_id=product["slug"], _external=True)
    sent = 0
    for alert in store.pending_stock_alerts(product_id):
        text, html = _render("back_in_stock", alert["locale"], product=product, product_url=product_url)
        subject = translate("email_back_in_stock_subject", alert["locale"]).format(name=product["name"])
        send_email(alert["email"], subject, text, html, kind="back_in_stock")
        store.mark_stock_alert_notified(alert["id"])
        sent += 1
    return sent


def owner_address():
    """The shop owner's address from Admin → Settings, or None while it is unset or a placeholder."""
    to_address = (store.get_config("site_settings.support_email") or "").strip()
    if not EMAIL_RE.match(to_address) or to_address.endswith(".example"):
        return None
    return to_address


def owner_locale() -> str:
    """Language of the owner's notifications (Admin → Settings)."""
    locale = store.get_config(OWNER_LOCALE_KEY) or "en"
    return locale if locale in SUPPORTED_LOCALES else "en"


def send_support_notification(message: dict, to_address: str) -> int:
    """Tell the shop owner about a new contact form message."""
    locale = owner_locale()
    text, html = _render(
        "support_new",
        locale,
        message=message,
        inbox_url=url_for("admin.message_detail", message_id=message["id"], _external=True),
    )
    subject = translate("email_owner_support_subject", locale).format(subject=message["subject"])
    return send_email(to_address, subject, text, html, kind="support_new")


def send_new_order_owner(order: dict):
    """Tell the shop owner about a new order; outbox id, or None while no owner address is set."""
    to_address = owner_address()
    if not to_address:
        return None
    locale = owner_locale()
    text, html = _render(
        "owner_new_order",
        locale,
        order=order,
        admin_url=url_for("admin.order_detail", order_id=order["id"], _external=True),
    )
    subject = translate("email_owner_order_subject", locale).format(
        id=order["id"], total=f"{order['total_price']:.2f}"
    )
    return send_email(to_address, subject, text, html, kind="owner_new_order")


def send_support_reply(message: dict) -> int:
    locale = message.get("locale") or "en"
    text, html = _render("support_reply", locale, message=message)
    subject = "Re: " + message["subject"]
    return send_email(message["email"], subject, text, html, kind="support_reply")
