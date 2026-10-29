"""
Delivering orders: the details a customer receives (account passwords,
codes) are stored as order deliveries, shown only on the tracking page and
cleared DELIVERY_RETENTION_DAYS later (from the request housekeeping hook in
app.py). Customer emails only say that the delivery is ready.
"""
from flask import current_app

DEFAULT_RETENTION_DAYS = 14
BODY_MAX = 5000


def retention_days() -> int:
    """Days delivered details stay readable on the tracking page (at least 1)."""
    return max(int(current_app.config.get("DELIVERY_RETENTION_DAYS") or DEFAULT_RETENTION_DAYS), 1)
