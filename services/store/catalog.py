"""
Listings, packages (variants) and categories; favourites and back-in-stock alerts.
"""
import json
from datetime import datetime, timezone

from services.database import get_db
from services.store.base import LOW_STOCK_THRESHOLD, _json_list, _parse_dt


def _product_from_row(row) -> dict:
    d = dict(row)
    d["order_fields"] = _json_list(d.get("order_fields"))
    d["created_at"] = _parse_dt(d.get("created_at")) or datetime.now(timezone.utc)
    d["updated_at"] = _parse_dt(d.get("updated_at")) or datetime.now(timezone.utc)
    d["avg_rating"] = round(float(d.get("avg_rating") or 0), 1)
    d["review_count"] = int(d.get("review_count") or 0)
    # A listing with packages is sold per package only: expose the cheapest
    # active package as base_price and the summed active-package stock as stock,
    # keeping the listing's own columns as own_price / own_stock. With every
    # package inactive the listing simply has nothing to sell (stock 0).
    d["has_variants"] = bool(d.get("variant_count"))
    d["price_from"] = False
    if d["has_variants"]:
        d["own_price"], d["own_stock"] = d.get("base_price"), d.get("stock")
        if d.get("min_price") is not None:
            d["base_price"] = float(d["min_price"])
            d["max_price"] = float(d["max_price"])
            d["price_from"] = d["max_price"] > d["base_price"]
        d["stock"] = int(d.get("variant_stock") or 0)
    img = d.get("image") or ""
    if img.startswith("/static/"):
        d["image"] = img
    elif img:
        d["image_path"] = img
    return d


def _category_from_row(row) -> dict:
    d = dict(row)
    d["created_at"] = _parse_dt(d.get("created_at")) or datetime.now(timezone.utc)
    return d


# Products always carry their published-review summary (avg_rating,
# review_count) and their active-package summary (see _product_from_row).
PRODUCT_SELECT = """
    SELECT p.*, r.avg_rating, r.review_count,
           v.variant_count, v.variant_stock, v.min_price, v.max_price
    FROM products p
    LEFT JOIN (
        SELECT product_id, AVG(rating) AS avg_rating, COUNT(*) AS review_count
        FROM reviews WHERE status = 'published' GROUP BY product_id
    ) r ON r.product_id = p.id
    LEFT JOIN (
        SELECT product_id, COUNT(*) AS variant_count,
               SUM(CASE WHEN active = 1 THEN stock ELSE 0 END) AS variant_stock,
               MIN(CASE WHEN active = 1 THEN price END) AS min_price,
               MAX(CASE WHEN active = 1 THEN price END) AS max_price
        FROM product_variants GROUP BY product_id
    ) v ON v.product_id = p.id
"""


EFFECTIVE_STOCK = "COALESCE(v.variant_stock, p.stock)"


def list_all_products() -> list:
    db = get_db()
    rows = db.execute(PRODUCT_SELECT + " ORDER BY p.id ASC").fetchall()
    return [_product_from_row(r) for r in rows]


def list_public_products() -> list:
    db = get_db()
    rows = db.execute(
        PRODUCT_SELECT + " WHERE p.status = 'active' ORDER BY p.id ASC"
    ).fetchall()
    return [_product_from_row(r) for r in rows]


def list_low_stock_products(threshold: int = LOW_STOCK_THRESHOLD) -> list:
    db = get_db()
    rows = db.execute(
        PRODUCT_SELECT
        + f" WHERE {EFFECTIVE_STOCK} <= ? ORDER BY {EFFECTIVE_STOCK} ASC, p.id ASC",
        (threshold,),
    ).fetchall()
    return [_product_from_row(r) for r in rows]


def _variant_from_row(row) -> dict:
    d = dict(row)
    d["active"] = bool(d["active"])
    d["price"] = float(d["price"])
    return d


def list_variants(product_id: int, active_only: bool = False) -> list:
    db = get_db()
    sql = "SELECT * FROM product_variants WHERE product_id = ?"
    if active_only:
        sql += " AND active = 1"
    rows = db.execute(sql + " ORDER BY sort_order ASC, price ASC, id ASC", (product_id,))
    return [_variant_from_row(r) for r in rows]


def list_low_stock_variants(threshold: int = LOW_STOCK_THRESHOLD) -> list:
    """Active packages running low, with their listing's name and status."""
    db = get_db()
    rows = db.execute(
        """
        SELECT v.*, p.name AS product_name, p.status AS product_status
        FROM product_variants v JOIN products p ON p.id = v.product_id
        WHERE v.active = 1 AND v.stock <= ?
        ORDER BY v.stock ASC, p.name ASC, v.sort_order ASC
        """,
        (threshold,),
    ).fetchall()
    return [_variant_from_row(r) for r in rows]


def get_variant(variant_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM product_variants WHERE id = ?", (variant_id,)).fetchone()
    return _variant_from_row(row) if row else None


def create_variant(
    product_id: int, name: str, price: float, stock: int, sort_order: int, active: bool,
    payment_url: str | None = None,
) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO product_variants
            (product_id, name, price, stock, sort_order, active, payment_url, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (product_id, name, float(price), int(stock), int(sort_order), 1 if active else 0,
         payment_url or None, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return cur.lastrowid


def update_variant(
    variant_id: int, name: str, price: float, stock: int, sort_order: int, active: bool,
    payment_url: str | None = None,
) -> None:
    db = get_db()
    db.execute(
        """
        UPDATE product_variants
        SET name = ?, price = ?, stock = ?, sort_order = ?, active = ?, payment_url = ?
        WHERE id = ?
        """,
        (name, float(price), int(stock), int(sort_order), 1 if active else 0,
         payment_url or None, variant_id),
    )
    db.commit()


def delete_variant(variant_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM product_variants WHERE id = ?", (variant_id,))
    db.commit()


def get_product(product_id: int):
    db = get_db()
    row = db.execute(PRODUCT_SELECT + " WHERE p.id = ?", (product_id,)).fetchone()
    return _product_from_row(row) if row else None


def get_product_by_slug(slug: str):
    db = get_db()
    row = db.execute(PRODUCT_SELECT + " WHERE p.slug = ?", (slug,)).fetchone()
    return _product_from_row(row) if row else None


def list_existing_slugs(exclude_id: int | None = None) -> set:
    db = get_db()
    if exclude_id is None:
        rows = db.execute("SELECT slug FROM products").fetchall()
    else:
        rows = db.execute(
            "SELECT slug FROM products WHERE id != ?", (exclude_id,)
        ).fetchall()
    return {r["slug"] for r in rows if r["slug"]}


def create_product(data: dict) -> int:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    img = data.get("image_path") or data.get("image") or ""
    cur = db.execute(
        """
        INSERT INTO products (
            name, slug, description, base_price, base_currency, listing_language,
            category, image, stock, status, owner, created_at, updated_at, payment_url,
            delivery_hours, order_fields
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data["name"],
            data["slug"],
            data.get("description", ""),
            float(data["base_price"]),
            data.get("base_currency", "USD"),
            data.get("listing_language", "en"),
            data.get("category", ""),
            img,
            int(data.get("stock", 0)),
            data.get("status", "active"),
            data.get("owner", "admin"),
            now,
            now,
            data.get("payment_url") or None,
            data.get("delivery_hours"),
            json.dumps(data["order_fields"], ensure_ascii=False) if data.get("order_fields") else None,
        ),
    )
    db.commit()
    return cur.lastrowid


def update_product(product_id: int, data: dict) -> None:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    fields = []
    values = []
    mapping = {
        "name": "name",
        "slug": "slug",
        "description": "description",
        "base_price": "base_price",
        "base_currency": "base_currency",
        "listing_language": "listing_language",
        "category": "category",
        "stock": "stock",
        "status": "status",
        "payment_url": "payment_url",
        "delivery_hours": "delivery_hours",
    }
    for key, col in mapping.items():
        if key in data:
            fields.append(f"{col} = ?")
            values.append(data[key])
    if "order_fields" in data:
        fields.append("order_fields = ?")
        values.append(json.dumps(data["order_fields"], ensure_ascii=False) if data["order_fields"] else None)
    if "image_path" in data and data["image_path"]:
        fields.append("image = ?")
        values.append(data["image_path"])
    elif "image" in data:
        fields.append("image = ?")
        values.append(data["image"] or "")
    fields.append("updated_at = ?")
    values.append(now)
    values.append(product_id)
    db.execute(
        f"UPDATE products SET {', '.join(fields)} WHERE id = ?",
        values,
    )
    db.commit()


def delete_product(product_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM products WHERE id = ?", (product_id,))
    db.commit()


def list_categories() -> list:
    db = get_db()
    rows = db.execute("SELECT * FROM categories ORDER BY name ASC").fetchall()
    return [_category_from_row(r) for r in rows]


def get_category(category_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    return _category_from_row(row) if row else None


def get_category_by_slug(slug: str):
    db = get_db()
    row = db.execute("SELECT * FROM categories WHERE slug = ?", (slug,)).fetchone()
    return _category_from_row(row) if row else None


def category_slugs_excluding(exclude_id: int | None = None) -> set:
    db = get_db()
    if exclude_id is None:
        rows = db.execute("SELECT slug FROM categories").fetchall()
    else:
        rows = db.execute(
            "SELECT slug FROM categories WHERE id != ?", (exclude_id,)
        ).fetchall()
    return {r["slug"] for r in rows if r["slug"]}


def create_category(name: str, slug: str, parent_id=None) -> int:
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    cur = db.execute(
        """
        INSERT INTO categories (name, slug, parent_id, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (name, slug, parent_id, now),
    )
    db.commit()
    return cur.lastrowid


def update_category(category_id: int, name: str, slug: str, parent_id) -> None:
    """Rename a category; listings (linked by name) follow the new name."""
    db = get_db()
    old = db.execute(
        "SELECT name FROM categories WHERE id = ?", (category_id,)
    ).fetchone()
    db.execute(
        "UPDATE categories SET name = ?, slug = ?, parent_id = ? WHERE id = ?",
        (name, slug, parent_id, category_id),
    )
    if old and old["name"] != name:
        db.execute(
            "UPDATE products SET category = ? WHERE category = ?",
            (name, old["name"]),
        )
    db.commit()


def delete_category(category_id: int) -> None:
    db = get_db()
    db.execute("DELETE FROM categories WHERE id = ?", (category_id,))
    db.commit()


def wishlist_product_ids(user_id: int) -> set:
    db = get_db()
    rows = db.execute("SELECT product_id FROM wishlist_items WHERE user_id = ?", (user_id,))
    return {r["product_id"] for r in rows}


def toggle_wishlist(user_id: int, product_id: int) -> bool:
    """Add or remove a favourite; returns True when it is now saved."""
    db = get_db()
    removed = db.execute(
        "DELETE FROM wishlist_items WHERE user_id = ? AND product_id = ?", (user_id, product_id)
    ).rowcount
    if not removed:
        db.execute(
            "INSERT INTO wishlist_items (user_id, product_id, created_at) VALUES (?, ?, ?)",
            (user_id, product_id, datetime.now(timezone.utc).isoformat()),
        )
    db.commit()
    return not removed


def list_wishlist_products(user_id: int) -> list:
    """Saved listings that are still public, most recently saved first."""
    db = get_db()
    rows = db.execute(
        PRODUCT_SELECT
        + """
        JOIN wishlist_items w ON w.product_id = p.id
        WHERE w.user_id = ? AND p.status = 'active'
        ORDER BY w.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [_product_from_row(r) for r in rows]


def add_stock_alert(product_id: int, email: str, locale: str) -> None:
    """Register (or re-arm) an alert for this email and listing."""
    db = get_db()
    db.execute(
        """
        INSERT INTO stock_alerts (product_id, email, locale, created_at) VALUES (?, ?, ?, ?)
        ON CONFLICT (product_id, email) DO UPDATE SET
            locale = excluded.locale, created_at = excluded.created_at, notified_at = NULL
        """,
        (product_id, email.strip().lower(), locale or "en", datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def pending_stock_alerts(product_id: int) -> list:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM stock_alerts WHERE product_id = ? AND notified_at IS NULL ORDER BY id",
        (product_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_pending_stock_alerts() -> dict:
    """{product_id: waiting alerts} for the admin listings table."""
    db = get_db()
    rows = db.execute(
        "SELECT product_id, COUNT(*) AS c FROM stock_alerts WHERE notified_at IS NULL GROUP BY product_id"
    )
    return {r["product_id"]: int(r["c"]) for r in rows}


def mark_stock_alert_notified(alert_id: int) -> None:
    db = get_db()
    db.execute(
        "UPDATE stock_alerts SET notified_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), alert_id),
    )
    db.commit()
