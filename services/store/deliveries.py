"""
Order deliveries: the account details or codes a customer receives. They are
shown on the order's tracking page only (emails just say they are ready) and
cleared after the retention period, so they do not linger in the database.

Code stock: codes / licence keys uploaded per listing or package, handed out
automatically when the payment of an order is confirmed.
"""
from datetime import datetime, timedelta, timezone

from services.database import get_db
from services.store.base import _parse_dt
from services.store.orders import _add_order_event, _order_items_for


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
    clear_delivered_codes(db)
    db.commit()
    return purged


def clear_delivered_codes(db) -> None:
    """Forget the codes handed out with deliveries that have been cleared (caller commits)."""
    db.execute(
        """
        UPDATE delivery_codes SET code = NULL
        WHERE code IS NOT NULL AND order_id IN (
            SELECT order_id FROM order_deliveries WHERE source = 'auto' AND purged_at IS NOT NULL
        )
        """
    )


# -----------------------------------------------------------------------------
# Code stock
# -----------------------------------------------------------------------------
def _adjust_stock(db, product_id: int, variant_id: int | None, delta: int) -> None:
    if variant_id:
        db.execute(
            "UPDATE product_variants SET stock = MAX(stock + ?, 0) WHERE id = ? AND product_id = ?",
            (delta, variant_id, product_id),
        )
    else:
        db.execute("UPDATE products SET stock = MAX(stock + ?, 0) WHERE id = ?", (delta, product_id))


def add_codes(product_id: int, variant_id: int | None, codes: list) -> int:
    """Add codes to a listing's (or package's) stock, skipping ones it already has.

    The listing/package stock grows by the number added, so every code can be
    sold. Returns how many were added.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    known = {
        r["code"] for r in db.execute(
            "SELECT code FROM delivery_codes WHERE product_id = ? AND variant_id IS ? AND code IS NOT NULL",
            (product_id, variant_id),
        )
    }
    new = [c for c in dict.fromkeys(codes) if c not in known]
    try:
        db.executemany(
            "INSERT INTO delivery_codes (product_id, variant_id, code, created_at) VALUES (?, ?, ?, ?)",
            [(product_id, variant_id, c, now) for c in new],
        )
        _adjust_stock(db, product_id, variant_id, len(new))
    except Exception:
        db.rollback()
        raise
    db.commit()
    return len(new)


def delete_code(product_id: int, code_id: int) -> bool:
    """Remove an unused code of the listing (its stock shrinks by one)."""
    db = get_db()
    row = db.execute(
        "SELECT variant_id FROM delivery_codes WHERE id = ? AND product_id = ? AND order_id IS NULL",
        (code_id, product_id),
    ).fetchone()
    if not row:
        return False
    try:
        db.execute("DELETE FROM delivery_codes WHERE id = ? AND order_id IS NULL", (code_id,))
        _adjust_stock(db, product_id, row["variant_id"], -1)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return True


def list_code_stock(product_id: int) -> dict:
    """Unused codes of a listing by package: {variant_id or None: [{id, code}]}."""
    db = get_db()
    stock = {}
    for r in db.execute(
        """
        SELECT id, variant_id, code FROM delivery_codes
        WHERE product_id = ? AND order_id IS NULL ORDER BY id
        """,
        (product_id,),
    ):
        stock.setdefault(r["variant_id"], []).append({"id": r["id"], "code": r["code"]})
    return stock


def count_codes_for_order(order_id: int) -> int:
    db = get_db()
    return int(db.execute(
        "SELECT COUNT(*) AS c FROM delivery_codes WHERE order_id = ?", (order_id,)
    ).fetchone()["c"])


def deliver_from_code_stock(order_id: int, retention_days: int) -> dict:
    """Hand out codes for every order line whose listing/package has code stock.

    One transaction: either every such line gets its full quantity of codes
    (taken with a compare-and-set, so two confirmations never share a code)
    or none does. Returns {"status": ...}:
      none     no line has code stock (manual delivery as before)
      already  the order already received codes
      short    a line has too few codes left ("name" says which); nothing taken
      delivered codes handed out; "complete" is True when every line was covered
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    items = _order_items_for(order_id)
    if not db.in_transaction:
        # Take the write lock before checking, so a second confirmation waits
        # and then sees this one's codes ("already")
        db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM delivery_codes WHERE order_id = ? LIMIT 1", (order_id,)).fetchone():
            db.rollback()
            return {"status": "already"}
        sections = []
        for item in items:
            pool = (item["product_id"], item["variant_id"])
            if not db.execute(
                "SELECT 1 FROM delivery_codes WHERE product_id = ? AND variant_id IS ? LIMIT 1", pool
            ).fetchone():
                continue
            claimed = db.execute(
                """
                UPDATE delivery_codes SET order_id = ?, assigned_at = ?
                WHERE id IN (
                    SELECT id FROM delivery_codes
                    WHERE product_id = ? AND variant_id IS ? AND order_id IS NULL AND code IS NOT NULL
                    ORDER BY id LIMIT ?
                ) AND order_id IS NULL
                """,
                (order_id, now.isoformat(), *pool, item["quantity"]),
            ).rowcount
            if claimed < item["quantity"]:
                db.rollback()
                return {"status": "short", "name": item["display_name"]}
            codes = [r["code"] for r in db.execute(
                """
                SELECT code FROM delivery_codes
                WHERE order_id = ? AND product_id = ? AND variant_id IS ? ORDER BY id
                """,
                (order_id, *pool),
            )]
            sections.append(item["display_name"] + ":\n" + "\n".join(codes))
        if not sections:
            db.rollback()
            return {"status": "none"}
        _insert_delivery(db, order_id, "\n\n".join(sections), "auto", retention_days, now)
    except Exception:
        db.rollback()
        raise
    db.commit()
    return {"status": "delivered", "complete": len(sections) == len(items)}
