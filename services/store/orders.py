"""
Orders: checkout (atomic stock/coupon reservation), status changes, payments, sales report.
"""
from datetime import datetime, timedelta, timezone

from services.database import get_db
from services.store.base import CouponUnavailableError, OrderChangedError, OutOfStockError, PaymentNotAllowedError, _like, _parse_dt


def _order_items_for(order_id: int) -> list:
    db = get_db()
    rows = db.execute(
        """
        SELECT product_id, product_name, quantity, unit_price_usd, variant_id, variant_name
        FROM order_items WHERE order_id = ?
        """,
        (order_id,),
    ).fetchall()
    return [
        {
            "product_id": r["product_id"],
            "product_name": r["product_name"],
            "variant_id": r["variant_id"],
            "variant_name": r["variant_name"],
            "display_name": r["product_name"] + (f" – {r['variant_name']}" if r["variant_name"] else ""),
            "quantity": r["quantity"],
            "price": float(r["unit_price_usd"]),
        }
        for r in rows
    ]


def _order_from_row(row, include_items: bool) -> dict:
    oid = row["id"]
    d = {
        "id": oid,
        "tracking_token": row["tracking_token"],
        "user_email": row["user_email"],
        "full_name": row["full_name"],
        "notes": row["notes"] or "",
        "total_price": float(row["total_price"]),
        "total_currency": row["total_currency"] or "USD",
        "fulfillment_status": row["fulfillment_status"] or "pending",
        "tracking_number": row["tracking_number"],
        "carrier_note": row["carrier_note"],
        "user_id": row["user_id"],
        "locale": row["locale"] or "en",
        "coupon_code": row["coupon_code"],
        "discount_usd": float(row["discount_usd"] or 0),
        "created_at": _parse_dt(row["created_at"]),
        "updated_at": _parse_dt(row["updated_at"]),
        "payment_due_at": _parse_dt(row["payment_due_at"]),
        "paid_at": _parse_dt(row["paid_at"]),
        "payment_ref": row["payment_ref"],
        "terms_accepted_at": _parse_dt(row["terms_accepted_at"]),
        "terms_version": row["terms_version"],
    }
    if include_items:
        # Use line_items — dict.items would shadow Jinja attribute access
        d["line_items"] = _order_items_for(oid)
    return d


def create_order(
    *,
    tracking_token: str,
    user_email: str,
    full_name: str,
    notes: str,
    total_price: float,
    total_currency: str,
    line_items: list,
    user_id: int | None = None,
    locale: str = "en",
    coupon: dict | None = None,
    discount_usd: float = 0.0,
    payment_window_hours: float = 0,
    terms_version: str | None = None,
) -> int:
    """
    line_items: list of dicts with product_id, product_name, quantity, unit_price_usd
    user_id: the signed-in customer's id, or None for guest checkout.
    locale: UI language at checkout (used for order emails).
    coupon / discount_usd: applied discount code; total_price is already net.
    payment_window_hours: unpaid orders are cancelled automatically this long
    after checkout (see cancel_overdue_order); 0 = no deadline.
    terms_version: version of the legal texts the customer accepted (recorded
    with the time of the order).

    Stock and the coupon use are reserved in the same transaction; raises
    OutOfStockError or CouponUnavailableError (and rolls back) otherwise.
    """
    db = get_db()
    created = datetime.now(timezone.utc)
    now = created.isoformat()
    payment_due_at = (
        (created + timedelta(hours=payment_window_hours)).isoformat()
        if payment_window_hours and payment_window_hours > 0
        else None
    )
    try:
        _reserve_stock(db, line_items)
        if coupon:
            used = db.execute(
                """
                UPDATE coupons SET used_count = used_count + 1
                WHERE id = ? AND active = 1 AND (max_uses IS NULL OR used_count < max_uses)
                """,
                (coupon["id"],),
            )
            if used.rowcount != 1:
                raise CouponUnavailableError(coupon["code"])
        cur = db.execute(
            """
            INSERT INTO orders (
                tracking_token, user_email, full_name, notes,
                total_price, total_currency, fulfillment_status,
                tracking_number, carrier_note, user_id, stock_reserved, locale,
                coupon_code, discount_usd, created_at, updated_at, payment_due_at,
                terms_accepted_at, terms_version
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tracking_token,
                user_email.strip().lower(),
                full_name.strip(),
                (notes or "").strip(),
                float(total_price),
                total_currency or "USD",
                user_id,
                locale or "en",
                coupon["code"] if coupon else None,
                round(float(discount_usd or 0), 2),
                now,
                now,
                payment_due_at,
                now if terms_version else None,
                terms_version,
            ),
        )
        order_id = cur.lastrowid
        for li in line_items:
            db.execute(
                """
                INSERT INTO order_items
                    (order_id, product_id, product_name, quantity, unit_price_usd, variant_id, variant_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    int(li["product_id"]),
                    li["product_name"],
                    int(li["quantity"]),
                    float(li["unit_price_usd"]),
                    li.get("variant_id"),
                    li.get("variant_name"),
                ),
            )
    except Exception:
        db.rollback()
        raise
    db.commit()
    return order_id


def _reserve_stock(db, line_items: list, require_active: bool = True) -> None:
    """
    Decrement stock for each line (package stock when the line has a
    variant_id); raise OutOfStockError if a line cannot be covered.
    """
    for li in line_items:
        qty = int(li["quantity"])
        if li.get("variant_id"):
            active_clause = (
                "AND active = 1 AND product_id IN (SELECT id FROM products WHERE status = 'active')"
                if require_active else ""
            )
            cur = db.execute(
                f"""
                UPDATE product_variants SET stock = stock - ?
                WHERE id = ? AND product_id = ? AND stock >= ? {active_clause}
                """,
                (qty, int(li["variant_id"]), int(li["product_id"]), qty),
            )
        else:
            # A new order line without a package can never buy a listing that
            # is sold per package (its own stock/price are not for sale).
            status_clause = (
                "AND status = 'active' AND NOT EXISTS "
                "(SELECT 1 FROM product_variants WHERE product_id = products.id)"
                if require_active else ""
            )
            cur = db.execute(
                f"UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ? {status_clause}",
                (qty, int(li["product_id"]), qty),
            )
        if cur.rowcount != 1:
            name = li["product_name"]
            if li.get("variant_name"):
                name += f" – {li['variant_name']}"
            raise OutOfStockError(name)


def _release_stock(db, line_items: list) -> None:
    """Return stock for each line (products or packages deleted since are skipped)."""
    for li in line_items:
        if li.get("variant_id"):
            db.execute(
                "UPDATE product_variants SET stock = stock + ? WHERE id = ?",
                (int(li["quantity"]), int(li["variant_id"])),
            )
        else:
            db.execute(
                "UPDATE products SET stock = stock + ? WHERE id = ?",
                (int(li["quantity"]), int(li["product_id"])),
            )


def get_order_by_id(order_id: int, include_items: bool = True):
    db = get_db()
    row = db.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return _order_from_row(row, include_items) if row else None


def get_order_by_tracking_token(token: str, include_items: bool = True):
    db = get_db()
    row = db.execute(
        "SELECT * FROM orders WHERE tracking_token = ?", (token,)
    ).fetchone()
    return _order_from_row(row, include_items) if row else None


def list_orders_for_user(user_id: int) -> list:
    """Orders placed while signed in to this account (guest orders stay token-only)."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    return [_order_from_row(r, True) for r in rows]


def list_orders_admin(limit: int = 200) -> list:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [_order_from_row(r, False) for r in rows]


def _order_filters(status: str | None, q: str | None):
    where, params = [], []
    if status:
        where.append("fulfillment_status = ?")
        params.append(status)
    q = (q or "").strip().lower()
    if q:
        pattern = _like(q)
        clause = (
            "(lower(user_email) LIKE ? ESCAPE '\\' OR lower(full_name) LIKE ? ESCAPE '\\'"
            " OR lower(tracking_token) LIKE ? ESCAPE '\\'"
            " OR lower(COALESCE(tracking_number, '')) LIKE ? ESCAPE '\\'"
            " OR lower(COALESCE(payment_ref, '')) LIKE ? ESCAPE '\\'"
        )
        params += [pattern] * 5
        if q.lstrip("#").isdigit():
            clause += " OR id = ?"
            params.append(int(q.lstrip("#")))
        where.append(clause + ")")
    return ("WHERE " + " AND ".join(where)) if where else "", params


def count_orders_filtered(status: str | None = None, q: str | None = None) -> int:
    sql_where, params = _order_filters(status, q)
    db = get_db()
    return int(db.execute(f"SELECT COUNT(*) AS c FROM orders {sql_where}", params).fetchone()["c"])


def search_orders(
    status: str | None = None,
    q: str | None = None,
    limit: int = 25,
    offset: int = 0,
    include_items: bool = False,
) -> list:
    """Newest-first orders matching status and a free-text query (id, email, name, tokens)."""
    sql_where, params = _order_filters(status, q)
    db = get_db()
    rows = db.execute(
        f"SELECT * FROM orders {sql_where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return [_order_from_row(r, include_items) for r in rows]


def count_orders_by_status() -> dict:
    db = get_db()
    rows = db.execute(
        "SELECT fulfillment_status AS s, COUNT(*) AS c FROM orders GROUP BY fulfillment_status"
    ).fetchall()
    return {r["s"]: int(r["c"]) for r in rows}


def _add_order_event(
    db, order_id: int, event_type: str, now: str, *,
    from_status=None, to_status=None, note=None, visible: bool = True,
) -> None:
    db.execute(
        """
        INSERT INTO order_events
            (order_id, event_type, from_status, to_status, note, visible_to_customer, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (order_id, event_type, from_status, to_status, note, 1 if visible else 0, now),
    )


def add_order_internal_note(order_id: int, note: str) -> None:
    """Admin-only note on the order timeline (never shown to the customer)."""
    db = get_db()
    _add_order_event(
        db, order_id, "internal_note", datetime.now(timezone.utc).isoformat(),
        note=note, visible=False,
    )
    db.commit()


def list_order_events(order_id: int, customer_view: bool = False) -> list:
    """Timeline entries, oldest first; customer_view hides internal notes."""
    db = get_db()
    sql = "SELECT * FROM order_events WHERE order_id = ?"
    if customer_view:
        sql += " AND visible_to_customer = 1"
    rows = db.execute(sql + " ORDER BY id ASC", (order_id,)).fetchall()
    events = []
    for r in rows:
        d = dict(r)
        d["created_at"] = _parse_dt(d["created_at"])
        events.append(d)
    return events


def count_orders() -> int:
    db = get_db()
    r = db.execute("SELECT COUNT(*) AS c FROM orders").fetchone()
    return int(r["c"])


def count_orders_for_user(user_id: int) -> int:
    db = get_db()
    r = db.execute(
        "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(r["c"])


def sales_report(days: int = 30) -> dict:
    """
    Order figures for the last `days` days (UTC), cancelled orders excluded:
    a per-day series, totals, top products and visitor conversion.
    """
    db = get_db()
    today = datetime.now(timezone.utc).date()
    first_day = today - timedelta(days=days - 1)
    since = first_day.isoformat()
    per_day = {
        r["day"]: r
        for r in db.execute(
            """
            SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS orders,
                   SUM(total_price) AS value, SUM(discount_usd) AS discount
            FROM orders
            WHERE created_at >= ? AND fulfillment_status != 'cancelled'
            GROUP BY day
            """,
            (since,),
        )
    }
    series = []
    for offset in range(days):
        day = (first_day + timedelta(days=offset)).isoformat()
        row = per_day.get(day)
        series.append({
            "date": day,
            "orders": int(row["orders"]) if row else 0,
            "value": round(float(row["value"] or 0), 2) if row else 0.0,
        })
    orders = sum(d["orders"] for d in series)
    value = round(sum(d["value"] for d in series), 2)
    discount = round(sum(float(r["discount"] or 0) for r in per_day.values()), 2)
    completed = db.execute(
        """
        SELECT COALESCE(SUM(total_price), 0) AS revenue FROM orders
        WHERE created_at >= ? AND fulfillment_status = 'completed'
        """,
        (since,),
    ).fetchone()["revenue"]
    top_products = [
        dict(r)
        for r in db.execute(
            """
            SELECT i.product_name || COALESCE(' – ' || i.variant_name, '') AS name,
                   SUM(i.quantity) AS quantity,
                   ROUND(SUM(i.quantity * i.unit_price_usd), 2) AS gross
            FROM order_items i JOIN orders o ON o.id = i.order_id
            WHERE o.created_at >= ? AND o.fulfillment_status != 'cancelled'
            GROUP BY i.product_id, i.product_name, i.variant_name
            ORDER BY gross DESC LIMIT 5
            """,
            (since,),
        )
    ]
    # Visitor-days (unique IPs per day, summed) approximate sessions better than
    # all-time unique IPs, so the conversion rate stays meaningful.
    visitors = db.execute(
        """
        SELECT COUNT(*) AS c FROM (
            SELECT DISTINCT substr(ts, 1, 10) AS day, ip FROM visits WHERE ts >= ?
        )
        """,
        (since,),
    ).fetchone()["c"]
    return {
        "days": days,
        "series": series,
        "max_value": max((d["value"] for d in series), default=0),
        "orders": orders,
        "value": value,
        "completed_revenue": round(float(completed), 2),
        "average_order": round(value / orders, 2) if orders else 0.0,
        "discount": discount,
        "visitors": int(visitors),
        # None ("—") when the visit data cannot support a rate: no visits, or
        # more orders than visitor-days (e.g. orders older than the kept visits)
        "conversion": round(orders / visitors * 100, 1) if visitors and orders <= visitors else None,
        "top_products": top_products,
    }


def update_order_fulfillment(
    order_id: int,
    *,
    fulfillment_status: str,
    tracking_number: str | None,
    carrier_note: str | None,
    actor: str = "admin",
    expected_status: str | None = None,
) -> list:
    """
    Cancelling an order returns its stock; un-cancelling reserves it again
    (raises OutOfStockError if the stock is no longer available). Orders placed
    before stock tracking (stock_reserved = 0) never touch stock.
    expected_status: the status the caller based its decision on; raises
    OrderChangedError when the order has moved on since (stale form, double
    submit, concurrent change).
    Each change is added to the order timeline (a customer's own cancellation
    as "cancelled_by_customer", an automatic one for non-payment, actor
    "system", as "expired"). Returns the list of event types recorded
    (empty when nothing changed or the order does not exist).
    Moving an order back to pending removes its payment deadline: from then
    on the admin decides, it is never cancelled automatically.
    """
    db = get_db()
    row = db.execute(
        """
        SELECT fulfillment_status, stock_reserved, tracking_number, carrier_note, coupon_code
        FROM orders WHERE id = ?
        """,
        (order_id,),
    ).fetchone()
    if not row:
        return []
    old_status = row["fulfillment_status"]
    if expected_status is not None and expected_status != old_status:
        raise OrderChangedError(order_id)
    was_cancelled = old_status == "cancelled"
    now_cancelled = fulfillment_status == "cancelled"
    now = datetime.now(timezone.utc).isoformat()
    recorded = []
    try:
        if old_status != fulfillment_status:
            # Compare-and-set: of two simultaneous changes from the same status
            # (e.g. a customer and an admin cancelling at once) only the first
            # wins, so stock is returned or reserved exactly once.
            claimed = db.execute(
                "UPDATE orders SET fulfillment_status = ? WHERE id = ? AND fulfillment_status = ?",
                (fulfillment_status, order_id, old_status),
            ).rowcount
            if claimed != 1:
                raise OrderChangedError(order_id)
        if row["stock_reserved"] and was_cancelled != now_cancelled:
            items = _order_items_for(order_id)
            if now_cancelled:
                _release_stock(db, items)
            else:
                _reserve_stock(db, items, require_active=False)
        if row["coupon_code"] and was_cancelled != now_cancelled:
            # A cancelled order gives its discount-code use back (so a
            # single-use code can be used again); re-opening takes it again.
            db.execute(
                "UPDATE coupons SET used_count = MAX(used_count + ?, 0) WHERE code = ?",
                (-1 if now_cancelled else 1, row["coupon_code"]),
            )
        if old_status != fulfillment_status:
            event = {"customer": "cancelled_by_customer", "system": "expired"}.get(
                actor, "status_changed"
            )
            _add_order_event(
                db, order_id, event, now,
                from_status=old_status, to_status=fulfillment_status,
            )
            recorded.append("status_changed")
        if tracking_number and tracking_number != row["tracking_number"]:
            _add_order_event(db, order_id, "tracking_updated", now, note=tracking_number)
            recorded.append("tracking_updated")
        if carrier_note and carrier_note != row["carrier_note"]:
            _add_order_event(db, order_id, "customer_note", now, note=carrier_note)
            recorded.append("customer_note")
        reopened = old_status != fulfillment_status and fulfillment_status == "pending"
        db.execute(
            """
            UPDATE orders SET
                fulfillment_status = ?,
                tracking_number = ?,
                carrier_note = ?,
                updated_at = ?,
                payment_due_at = CASE WHEN ? THEN NULL ELSE payment_due_at END
            WHERE id = ?
            """,
            (fulfillment_status, tracking_number, carrier_note, now, int(reopened), order_id),
        )
    except Exception:
        db.rollback()
        raise
    db.commit()
    return recorded


def list_overdue_unpaid_orders(now: datetime, limit: int = 50) -> list:
    """Pending orders whose payment deadline has passed, oldest deadline first."""
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM orders
        WHERE fulfillment_status = 'pending' AND paid_at IS NULL
          AND payment_due_at IS NOT NULL AND payment_due_at <= ?
        ORDER BY payment_due_at ASC, id ASC
        LIMIT ?
        """,
        (now.astimezone(timezone.utc).isoformat(), limit),
    ).fetchall()
    return [_order_from_row(r, include_items=True) for r in rows]


def record_payment(order_id: int, reference: str | None = None) -> list:
    """Confirm the payment of an order; a pending order moves on to processing.

    One transaction, claimed with a compare-and-set on paid_at, so a double
    click records it once and the automatic expiry can no longer cancel the
    order (whichever commits first wins; the other sees the change).
    Raises OrderChangedError when it is already paid, PaymentNotAllowedError
    when it is cancelled. Returns the event types recorded.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    reference = (reference or "").strip()[:120] or None
    try:
        row = db.execute("SELECT fulfillment_status FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return []
        if row["fulfillment_status"] == "cancelled":
            raise PaymentNotAllowedError(order_id)
        claimed = db.execute(
            """
            UPDATE orders SET paid_at = ?, payment_ref = ?, updated_at = ?
            WHERE id = ? AND paid_at IS NULL AND fulfillment_status != 'cancelled'
            """,
            (now, reference, now, order_id),
        ).rowcount
        if claimed != 1:
            raise OrderChangedError(order_id)
        _add_order_event(db, order_id, "payment_received", now, note=reference)
        recorded = ["payment_received"]
        moved = db.execute(
            "UPDATE orders SET fulfillment_status = 'processing' WHERE id = ? AND fulfillment_status = 'pending'",
            (order_id,),
        ).rowcount
        if moved:
            _add_order_event(db, order_id, "status_changed", now, from_status="pending", to_status="processing")
            recorded.append("status_changed")
    except Exception:
        db.rollback()
        raise
    db.commit()
    return recorded


def undo_payment(order_id: int) -> bool:
    """Take back a payment confirmation made by mistake (status is left as it is)."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    undone = db.execute(
        "UPDATE orders SET paid_at = NULL, payment_ref = NULL, updated_at = ? WHERE id = ? AND paid_at IS NOT NULL",
        (now, order_id),
    ).rowcount
    if undone:
        _add_order_event(db, order_id, "payment_undone", now, visible=False)
    db.commit()
    return bool(undone)
