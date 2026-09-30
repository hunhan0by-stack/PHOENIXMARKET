"""
Discount codes.
"""
import sqlite3
from datetime import datetime, timezone

from services.database import get_db
from services.store.base import _parse_dt


COUPON_FIELDS = ("code", "kind", "value", "min_order_usd", "max_uses", "starts_at", "expires_at", "active")


def _coupon_from_row(row) -> dict:
    d = dict(row)
    d["starts_at"] = _parse_dt(d["starts_at"])
    d["expires_at"] = _parse_dt(d["expires_at"])
    d["created_at"] = _parse_dt(d["created_at"])
    d["active"] = bool(d["active"])
    return d


def _coupon_values(data: dict) -> list:
    def iso(value):
        return value.isoformat() if isinstance(value, datetime) else value

    return [
        data["code"], data["kind"], float(data["value"]), float(data.get("min_order_usd") or 0),
        data.get("max_uses"), iso(data.get("starts_at")), iso(data.get("expires_at")),
        1 if data.get("active", True) else 0,
    ]


def list_coupons() -> list:
    db = get_db()
    return [_coupon_from_row(r) for r in db.execute("SELECT * FROM coupons ORDER BY id DESC")]


def get_coupon(coupon_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
    return _coupon_from_row(row) if row else None


def get_coupon_by_code(code: str):
    db = get_db()
    row = db.execute("SELECT * FROM coupons WHERE code = ?", ((code or "").strip().upper(),)).fetchone()
    return _coupon_from_row(row) if row else None


def create_coupon(data: dict) -> int | None:
    """Insert a coupon; returns None when the code already exists."""
    db = get_db()
    try:
        cur = db.execute(
            f"INSERT INTO coupons ({', '.join(COUPON_FIELDS)}, created_at) VALUES ({', '.join('?' * (len(COUPON_FIELDS) + 1))})",
            _coupon_values(data) + [datetime.now(timezone.utc).isoformat()],
        )
    except sqlite3.IntegrityError:
        db.rollback()
        return None
    db.commit()
    return cur.lastrowid


def update_coupon(coupon_id: int, data: dict) -> bool:
    """Update a coupon; returns False when the new code belongs to another coupon."""
    db = get_db()
    try:
        db.execute(
            f"UPDATE coupons SET {', '.join(f'{f} = ?' for f in COUPON_FIELDS)} WHERE id = ?",
            _coupon_values(data) + [coupon_id],
        )
    except sqlite3.IntegrityError:
        db.rollback()
        return False
    db.commit()
    return True


def set_coupon_active(coupon_id: int, active: bool) -> None:
    db = get_db()
    db.execute("UPDATE coupons SET active = ? WHERE id = ?", (1 if active else 0, coupon_id))
    db.commit()


def delete_coupon(coupon_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    db.commit()
