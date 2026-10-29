"""
KVKK self-service for customer accounts: a copy of the account's data, and
deleting the account (personal data that need not be kept is removed; orders
stay as commercial records, without the link to the account).

Only data tied to the account is included: emails are not verified at sign-up,
so guest orders that merely use the same address are never handed out.
"""
from datetime import datetime, timezone

from services.database import get_db
from services.store.deliveries import clear_delivered_codes, list_deliveries
from services.store.orders import list_orders_for_user


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def export_account_data(user_id: int) -> dict:
    """Everything stored for the account, as JSON-ready data (no password hash)."""
    db = get_db()
    user = db.execute("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return {}
    orders = [
        {
            "id": o["id"], "created_at": _iso(o["created_at"]), "status": o["fulfillment_status"],
            "full_name": o["full_name"], "email": o["user_email"], "notes": o["notes"],
            "total_usd": o["total_price"], "discount_usd": o["discount_usd"], "coupon": o["coupon_code"],
            "paid_at": _iso(o["paid_at"]), "terms_accepted_at": _iso(o["terms_accepted_at"]),
            "items": [
                {
                    "name": li["display_name"], "quantity": li["quantity"], "unit_price_usd": li["price"],
                    "details": li["inputs"],
                }
                for li in o["line_items"]
            ],
            # Not yet cleared by the retention period
            "deliveries": [
                {"delivered_at": _iso(d["created_at"]), "details": d["body"]}
                for d in list_deliveries(o["id"]) if d["body"] is not None
            ],
        }
        for o in list_orders_for_user(user_id)
    ]
    reviews = [dict(r) for r in db.execute(
        """
        SELECT p.name AS product, r.rating, r.body, r.reviewer_name, r.status, r.created_at
        FROM reviews r JOIN products p ON p.id = r.product_id WHERE r.user_id = ? ORDER BY r.id
        """,
        (user_id,),
    )]
    favourites = [r["name"] for r in db.execute(
        """
        SELECT p.name FROM wishlist_items w JOIN products p ON p.id = w.product_id
        WHERE w.user_id = ? ORDER BY w.created_at
        """,
        (user_id,),
    )]
    messages = [dict(r) for r in db.execute(
        "SELECT subject, message, order_ref, status, created_at FROM support_messages WHERE user_id = ? ORDER BY id",
        (user_id,),
    )]
    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {"email": user["email"], "created_at": user["created_at"]},
        "orders": orders,
        "reviews": reviews,
        "favourites": favourites,
        "support_messages": messages,
    }


def delete_account(user_id: int) -> bool:
    """Delete the account in one transaction; False when it no longer exists.

    Reviews, favourites and reset links go with it (ON DELETE CASCADE). Orders
    and support messages are kept for the statutory retention periods but no
    longer point to an account. Delivered details (account passwords, codes)
    of the account's orders are cleared at once.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            """
            UPDATE order_deliveries SET body = NULL, purged_at = ?
            WHERE purged_at IS NULL AND order_id IN (SELECT id FROM orders WHERE user_id = ?)
            """,
            (now, user_id),
        )
        clear_delivered_codes(db)
        db.execute("UPDATE orders SET user_id = NULL WHERE user_id = ?", (user_id,))
        db.execute("UPDATE support_messages SET user_id = NULL WHERE user_id = ?", (user_id,))
        deleted = db.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount
    except Exception:
        db.rollback()
        raise
    db.commit()
    return deleted == 1
