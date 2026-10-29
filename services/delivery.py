"""
Delivering orders: the details a customer receives (account passwords,
codes) are stored as order deliveries, shown only on the tracking page and
cleared DELIVERY_RETENTION_DAYS later (from the request housekeeping hook in
app.py). Customer emails only say that the delivery is ready.

Listings and packages with code stock are delivered automatically once the
admin confirms the payment.
"""
from flask import current_app

import services.audit as audit
import services.notifications as notifications
import services.store as store
from services.translations import t as translate

DEFAULT_RETENTION_DAYS = 14
BODY_MAX = 5000
CODE_MAX = 500
CODES_PER_UPLOAD = 1000


def retention_days() -> int:
    """Days delivered details stay readable on the tracking page (at least 1)."""
    return max(int(current_app.config.get("DELIVERY_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS), 1)


def parse_codes(raw: str):
    """(codes, error_key or None) from a pasted list, one code per line."""
    codes = [line.strip() for line in (raw or "").splitlines() if line.strip()]
    if not codes or len(codes) > CODES_PER_UPLOAD or any(len(c) > CODE_MAX for c in codes):
        return [], "flash_codes_invalid"
    return codes, None


def mark_completed(order: dict) -> bool:
    """Move a delivered order to completed; False when it changed in the meantime."""
    if order["fulfillment_status"] == "completed":
        return True
    try:
        store.update_order_fulfillment(
            order["id"],
            fulfillment_status="completed",
            tracking_number=order["tracking_number"],
            carrier_note=order["carrier_note"],
            expected_status=order["fulfillment_status"],
        )
    except (store.OrderChangedError, store.OutOfStockError):
        return False
    return True


def auto_deliver(order_id: int, notify: bool) -> tuple:
    """Deliver a just-paid order from code stock: (flash key or None, params).

    None when no line has code stock (the admin delivers by hand as before).
    When codes run short nothing is handed out and the order gets an internal
    note, so the admin can deliver by hand or add codes and deliver.
    """
    result = store.deliver_from_code_stock(order_id, retention_days())
    status = result["status"]
    if status in ("none", "already"):
        return None, {}
    if status == "short":
        # Internal timeline note in the language of the owner's notifications
        note = translate("note_auto_delivery_short", notifications.owner_locale())
        store.add_order_internal_note(order_id, note.format(name=result["name"]))
        audit.admin_event("order_auto_delivery_short", f"order #{order_id}")
        return "flash_auto_delivery_short", {"name": result["name"]}
    audit.admin_event("order_auto_delivered", f"order #{order_id}")
    if result["complete"]:
        mark_completed(store.get_order_by_id(order_id))
    if notify:
        try:
            notifications.send_delivery_ready(store.get_order_by_id(order_id))
        except Exception:  # noqa: BLE001 - the delivery itself is saved
            current_app.logger.exception("Delivery email failed for order %s", order_id)
    return ("flash_auto_delivered" if result["complete"] else "flash_auto_delivered_partly"), {}
