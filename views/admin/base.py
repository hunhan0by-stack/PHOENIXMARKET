"""
The admin blueprint, its login guard and helpers shared by several admin areas.
"""
import time

from flask import Blueprint, current_app, flash, redirect, request, session, url_for

import services.notifications as notifications
from services.ratelimit import RateLimiter
from services.web import ADMIN_SEEN_KEY, is_admin, tr_msg


bp = Blueprint("admin", __name__)


ORDER_STATUS_CHOICES = ("pending", "processing", "completed", "cancelled")
LISTING_STATUS_CHOICES = ("active", "inactive")
PUBLIC_ENDPOINTS = {"admin.login", "admin.login_verify", "admin.logout"}


# Brute-force protection for the admin login (per client IP)
admin_login_limiter = RateLimiter(max_hits=5, window_seconds=15 * 60)
# Wrong two-step codes from all addresses together. Reaching the code step
# means the password is known; per-IP limits alone would let many addresses
# guess the 6 digits (about one in 333,000 per try) within a day.
admin_2fa_global_limiter = RateLimiter(max_hits=20, window_seconds=15 * 60)
GLOBAL_2FA_KEY = "all"


@bp.before_request
def require_admin():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not is_admin():
        return redirect(url_for("admin.login"))
    session[ADMIN_SEEN_KEY] = time.time()
    return None


def notify_back_in_stock_safely(product_id: int) -> None:
    """Send back-in-stock emails; a mail problem must not break the admin action."""
    try:
        sent = notifications.notify_back_in_stock(product_id)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Back-in-stock emails failed for product %s", product_id)
        return
    if sent:
        flash(tr_msg("flash_stock_alerts_sent", n=sent), "info")
