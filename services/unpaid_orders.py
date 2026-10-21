"""
Unpaid orders: an order reserves stock (and a discount-code use) at checkout,
but payment happens later on itemsatis. Orders still pending when their
payment deadline (orders.payment_due_at, set at checkout from
PENDING_ORDER_TTL_HOURS) passes are cancelled so the stock can be sold again.

Runs from the request housekeeping hook in app.py (no scheduler on Render).
"""
from datetime import datetime, timezone

from flask import current_app

import services.notifications as notifications
import services.store as store

DEFAULT_PAYMENT_WINDOW_HOURS = 24


def payment_window_hours() -> float:
    """Hours a customer has to pay; 0 disables automatic cancellation."""
    return max(float(current_app.config.get("PENDING_ORDER_TTL_HOURS") or 0), 0.0)


def cancel_overdue_orders(now=None, limit: int = 50) -> list:
    """Cancel pending orders past their deadline; returns the cancelled order ids.

    Uses the same compare-and-set as every other status change, so an order the
    admin moves on (e.g. marks as processing after payment) at the same moment
    is left alone. Emails failing never undo a cancellation.
    """
    now = now or datetime.now(timezone.utc)
    cancelled = []
    for order in store.list_overdue_unpaid_orders(now, limit):
        try:
            changes = store.update_order_fulfillment(
                order["id"],
                fulfillment_status="cancelled",
                tracking_number=order["tracking_number"],
                carrier_note=order["carrier_note"],
                actor="system",
                expected_status="pending",
            )
        except store.OrderChangedError:
            continue
        except Exception:  # noqa: BLE001 - one bad order must not stop the others
            current_app.logger.exception("Cancelling unpaid order %s failed", order["id"])
            continue
        if not changes:
            continue
        cancelled.append(order["id"])
        try:
            notifications.send_order_update(store.get_order_by_id(order["id"]), [*changes, "expired"])
            for product_id in {li["product_id"] for li in order["line_items"]}:
                notifications.notify_back_in_stock(product_id)
        except Exception:  # noqa: BLE001
            current_app.logger.exception("Emails after cancelling unpaid order %s failed", order["id"])
    if cancelled:
        current_app.logger.info("Cancelled unpaid orders past their payment deadline: %s", cancelled)
    return cancelled
