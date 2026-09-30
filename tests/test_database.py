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
