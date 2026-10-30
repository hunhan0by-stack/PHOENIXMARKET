"""Schema migrations for databases created by earlier releases."""
import sqlite3

from app import app
import services.store as store
from services.database import init_schema

OLD_ORDERS_SCHEMA = """
CREATE TABLE orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, tracking_token TEXT NOT NULL UNIQUE,
    user_email TEXT NOT NULL, full_name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
    total_price REAL NOT NULL, total_currency TEXT NOT NULL DEFAULT 'USD',
    fulfillment_status TEXT NOT NULL DEFAULT 'pending', tracking_number TEXT,
    carrier_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT INTO orders (tracking_token, user_email, full_name, total_price, created_at, updated_at)
VALUES ('old-token', 'o@x.com', 'Old', 3, '2025-01-01', '2025-01-01');
"""


def test_old_orders_table_is_upgraded(tmp_path):
    old_db = tmp_path / "old.db"
    conn = sqlite3.connect(old_db)
    conn.executescript(OLD_ORDERS_SCHEMA)
    conn.close()

    saved = app.config["DATABASE_PATH"]
    app.config["DATABASE_PATH"] = str(old_db)
    try:
        with app.app_context():
            init_schema()
            init_schema()  # idempotent
            order = store.get_order_by_tracking_token("old-token")
    finally:
        app.config["DATABASE_PATH"] = saved

    conn = sqlite3.connect(old_db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(orders)")]
    reserved = conn.execute("SELECT stock_reserved FROM orders").fetchone()
    conn.close()
    assert "user_id" in cols and "stock_reserved" in cols
    assert order["user_id"] is None and reserved == (0,)


def _fresh_database(tmp_path, monkeypatch):
    from app import app
    from services.database import init_schema
    from tests.helpers import in_app

    monkeypatch.setitem(app.config, "DATABASE_PATH", str(tmp_path / "fresh.db"))
    in_app(init_schema)
    return in_app


def test_production_demo_catalog_starts_inactive(tmp_path, monkeypatch):
    """A fresh production disk must not put demo listings on sale."""
    import services.store as store

    in_app = _fresh_database(tmp_path, monkeypatch)
    in_app(store.seed_if_empty, "https://itemsatis.test", demo_listings_active=False)
    products = in_app(store.list_all_products)
    assert products and all(p["status"] == "inactive" for p in products)
    assert in_app(store.list_public_products) == []


def test_emptied_catalog_is_not_refilled_on_restart(tmp_path, monkeypatch):
    import services.store as store

    in_app = _fresh_database(tmp_path, monkeypatch)
    in_app(store.seed_if_empty, "https://itemsatis.test")
    for p in in_app(store.list_all_products):
        in_app(store.delete_product, p["id"])
    in_app(store.seed_if_empty, "https://itemsatis.test")  # the next start-up
    assert in_app(store.list_all_products) == []


def test_existing_catalog_from_before_the_marker_is_left_alone(tmp_path, monkeypatch):
    import services.store as store

    in_app = _fresh_database(tmp_path, monkeypatch)
    in_app(store.create_product, {"name": "Owner listing", "slug": "owner-listing", "base_price": 3})
    in_app(store.seed_if_empty, "https://itemsatis.test")
    assert [p["name"] for p in in_app(store.list_all_products)] == ["Owner listing"]
    in_app(store.delete_product, in_app(store.list_all_products)[0]["id"])
    in_app(store.seed_if_empty, "https://itemsatis.test")
    assert in_app(store.list_all_products) == []
