"""
Product reviews by customers with a completed order, and their moderation.
"""
from datetime import datetime, timezone

from services.database import get_db
from services.store.base import _parse_dt


REVIEW_STATUSES = ("published", "hidden")


def _review_from_row(row) -> dict:
    d = dict(row)
    d["created_at"] = _parse_dt(d["created_at"])
    d["updated_at"] = _parse_dt(d["updated_at"])
    return d


def user_can_review(user_id: int, product_id: int) -> bool:
    """True when the customer has a completed order containing the product."""
    db = get_db()
    row = db.execute(
        """
        SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.id
        WHERE o.user_id = ? AND i.product_id = ? AND o.fulfillment_status = 'completed'
        LIMIT 1
        """,
        (user_id, product_id),
    ).fetchone()
    return row is not None


def reviewer_display_name(user_id: int, email: str) -> str:
    """'Jane D.' from the customer's latest order name, else the email's local part."""
    db = get_db()
    row = db.execute(
        "SELECT full_name FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    parts = (row["full_name"] if row else "").split()
    if parts:
        return parts[0] + (f" {parts[-1][0].upper()}." if len(parts) > 1 else "")
    local = (email or "").split("@")[0]
    return (local[:1].upper() + "***") if local else "Customer"


def get_user_review(user_id: int, product_id: int):
    db = get_db()
    row = db.execute(
        "SELECT * FROM reviews WHERE user_id = ? AND product_id = ?", (user_id, product_id)
    ).fetchone()
    return _review_from_row(row) if row else None


def save_review(user_id: int, product_id: int, rating: int, body: str, reviewer_name: str) -> None:
    """Create or update the customer's review; moderation status is kept on edits."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """
        INSERT INTO reviews (product_id, user_id, rating, body, reviewer_name, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'published', ?, ?)
        ON CONFLICT (product_id, user_id) DO UPDATE SET
            rating = excluded.rating, body = excluded.body,
            reviewer_name = excluded.reviewer_name, updated_at = excluded.updated_at
        """,
        (product_id, user_id, rating, body, reviewer_name, now, now),
    )
    db.commit()


def delete_user_review(user_id: int, product_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM reviews WHERE user_id = ? AND product_id = ?", (user_id, product_id))
    db.commit()


def list_product_reviews(product_id: int, limit: int = 50) -> list:
    db = get_db()
    rows = db.execute(
        """
        SELECT * FROM reviews WHERE product_id = ? AND status = 'published'
        ORDER BY id DESC LIMIT ?
        """,
        (product_id, limit),
    ).fetchall()
    return [_review_from_row(r) for r in rows]


def count_reviews(status: str | None = None) -> int:
    db = get_db()
    if status:
        row = db.execute("SELECT COUNT(*) AS c FROM reviews WHERE status = ?", (status,)).fetchone()
    else:
        row = db.execute("SELECT COUNT(*) AS c FROM reviews").fetchone()
    return int(row["c"])


def list_reviews_admin(status: str | None = None, limit: int = 30, offset: int = 0) -> list:
    """Newest reviews with product name and customer email."""
    db = get_db()
    sql = """
        SELECT r.*, p.name AS product_name, p.slug AS product_slug, u.email AS user_email
        FROM reviews r
        JOIN products p ON p.id = r.product_id
        JOIN users u ON u.id = r.user_id
    """
    params = []
    if status:
        sql += " WHERE r.status = ?"
        params.append(status)
    rows = db.execute(sql + " ORDER BY r.id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    return [_review_from_row(r) for r in rows]


def set_review_status(review_id: int, status: str) -> None:
    db = get_db()
    db.execute("UPDATE reviews SET status = ? WHERE id = ?", (status, review_id))
    db.commit()


def delete_review(review_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM reviews WHERE id = ?", (review_id,))
    db.commit()
