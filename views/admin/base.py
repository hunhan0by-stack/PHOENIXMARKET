"""
The admin blueprint, its login guard and helpers shared by several admin areas.
"""
from flask import Blueprint, current_app, flash, redirect, request, url_for

import services.notifications as notifications
from services.ratelimit import RateLimiter
from services.web import is_admin, tr_msg


bp = Blueprint("admin", __name__)


ORDER_STATUS_CHOICES = ("pending", "processing", "completed", "cancelled")
LISTING_STATUS_CHOICES = ("active", "inactive")
PUBLIC_ENDPOINTS = {"admin.login", "admin.logout"}


# Brute-force protection for the admin login (per client IP)
admin_login_limiter = RateLimiter(max_hits=5, window_seconds=15 * 60)


@bp.before_request
def require_admin():
    if request.endpoint not in PUBLIC_ENDPOINTS and not is_admin():
        return redirect(url_for("admin.login"))
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
