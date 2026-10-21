"""
Discount codes: validation and discount calculation. Amounts are in USD (the
currency orders are stored in); the cart converts them for display.
"""
import re
from datetime import datetime, timezone

from services.money import money
import services.store as store

CODE_RE = re.compile(r"^[A-Z0-9_-]{3,32}$")
KINDS = ("percent", "fixed")


class CouponError(Exception):
    """A coupon cannot be used; key is the translation key of the reason."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def normalize_code(raw: str) -> str:
    return (raw or "").strip().upper()


def discount_for(coupon: dict, subtotal_usd: float) -> float:
    """Discount in USD, never more than the subtotal."""
    if coupon["kind"] == "percent":
        amount = subtotal_usd * min(float(coupon["value"]), 100.0) / 100.0
    else:
        amount = float(coupon["value"])
    return money(max(0.0, min(amount, subtotal_usd)))


def check_coupon(coupon, subtotal_usd: float, now: datetime | None = None) -> None:
    """Raise CouponError when the coupon cannot be used for this subtotal."""
    now = now or datetime.now(timezone.utc)
    if not coupon or not coupon["active"]:
        raise CouponError("flash_coupon_invalid")
    if coupon["starts_at"] and coupon["starts_at"] > now:
        raise CouponError("flash_coupon_not_started")
    if coupon["expires_at"] and coupon["expires_at"] <= now:
        raise CouponError("flash_coupon_expired")
    if coupon["max_uses"] is not None and coupon["used_count"] >= coupon["max_uses"]:
        raise CouponError("flash_coupon_used_up")
    if subtotal_usd < float(coupon["min_order_usd"] or 0):
        raise CouponError("flash_coupon_min_order", min_usd=float(coupon["min_order_usd"]))


def evaluate(code: str, subtotal_usd: float):
    """(coupon, discount_usd) for a code, or raise CouponError."""
    coupon = store.get_coupon_by_code(normalize_code(code))
    check_coupon(coupon, subtotal_usd)
    return coupon, discount_for(coupon, subtotal_usd)
