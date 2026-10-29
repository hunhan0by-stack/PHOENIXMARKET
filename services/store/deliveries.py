"""
Order deliveries: the account details or codes a customer receives. They are
shown on the order's tracking page only (emails just say they are ready) and
cleared after the retention period, so they do not linger in the database.
"""
from datetime import datetime, timedelta, timezone

from services.database import get_db
from services.store.base import _parse_dt
from services.store.orders import _add_order_event


def _delivery_from_row(row) -> dict:
    d = dict(row)
    for key in ("created_at", "purge_at", "purged_at"):
        d[key] = _parse_dt(d[key])
    return d


def _insert_delivery(db, order_id: int, body: str, source: str, retention_days: int, now: datetime) -> None:
    """Add a delivery and its timeline entry (which never contains the delivered details)."""
    db.execute(
        """
        INSERT INTO order_deliveries (order_id, body, source, created_at, purge_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (order_id, body, source, now.isoformat(), (now + timedelta(days=retention_days)).isoformat()),
    )
    _add_order_event(db, order_id, "delivered", now.isoformat())


def add_delivery(order_id: int, body: str, retention_days: int, source: str = "manual") -> None:
    db = get_db()
    try:
        _insert_delivery(db, order_id, body, source, retention_days, datetime.now(timezone.utc))
    except Exception:
        db.rollback()
        raise
    db.commit()


def list_deliveries(order_id: int) -> list:
    """The order's deliveries, oldest first (body None once purged)."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM order_deliveries WHERE order_id = ? ORDER BY id", (order_id,)
    ).fetchall()
    return [_delivery_from_row(r) for r in rows]


def purge_expired_deliveries(now: datetime | None = None) -> int:
    """Clear the details of deliveries past their retention; returns how many were cleared."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    db = get_db()
    purged = db.execute(
        """
        UPDATE order_deliveries SET body = NULL, purged_at = ?
        WHERE purged_at IS NULL AND purge_at <= ?
        """,
        (now, now),
    ).rowcount
    db.commit()
    return purged
