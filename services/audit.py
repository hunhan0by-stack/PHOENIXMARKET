"""
Recording security-relevant actions (the audit log, Admin → Two-step sign-in
page). Callers pass only non-secret details such as an order number or a
count, never passwords, codes, tokens or delivered details.

A failure to record never breaks the action itself; it is logged instead.
"""
from flask import current_app, has_request_context

import services.store as store
from services.web import client_ip


def admin_event(event: str, detail: str = "") -> None:
    """An admin action (or a failed admin sign-in): recorded with the client IP."""
    _record(event, "admin", detail, client_ip() if has_request_context() else None)


def customer_event(event: str, user_id: int, detail: str = "") -> None:
    """A customer's own data request (export, deletion): no IP is kept."""
    _record(event, f"customer #{user_id}", detail, None)


def system_event(event: str, actor: str, detail: str = "") -> None:
    """Actions from the command line or maintenance (no request, no IP)."""
    _record(event, actor, detail, None)


def _record(event: str, actor: str, detail: str, ip) -> None:
    try:
        store.record_security_event(event, actor, detail, ip)
    except Exception:  # noqa: BLE001 - auditing must not break the action
        current_app.logger.exception("Recording security event %s failed", event)
        return
    current_app.logger.info("security event: %s actor=%s %s", event, actor, detail)
