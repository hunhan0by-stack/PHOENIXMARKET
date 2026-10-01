"""
Shared helpers and errors: dates, slugs, LIKE escaping, exceptions.
"""
import re
from datetime import datetime, timezone


LOW_STOCK_THRESHOLD = 5


class OutOfStockError(Exception):
    """Raised when an order line cannot be fulfilled from current stock."""

    def __init__(self, product_name: str):
        super().__init__(product_name)
        self.product_name = product_name


class CouponUnavailableError(Exception):
    """Raised when a coupon was used up or disabled while the order was placed."""


class OrderChangedError(Exception):
    """Raised when an order's status was changed by someone else in the meantime."""


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "item"


def unique_slug(value: str, existing: set) -> str:
    """slugify(value), suffixed with -2, -3, ... until it is not in existing."""
    base = slugify(value)
    slug = base
    counter = 2
    while slug in existing:
        slug = f"{base}-{counter}"
        counter += 1
    return slug


def _parse_dt(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    s = str(val).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _like(text: str) -> str:
    """LIKE pattern matching text literally (use with ESCAPE '\\')."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class PaymentNotAllowedError(Exception):
    """The order is cancelled: re-open it (reserving its stock again) first."""
