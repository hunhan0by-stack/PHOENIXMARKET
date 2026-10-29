"""
SQLite database setup and Flask application context integration.
"""
import sqlite3
from pathlib import Path

from flask import current_app, g


def get_db():
    """Return a connection bound to the current application context."""
    if "db" not in g:
        path = current_app.config["DATABASE_PATH"]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        g.db = conn
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema():
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            parent_id INTEGER REFERENCES categories(id),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL DEFAULT '',
            base_price REAL NOT NULL,
            base_currency TEXT NOT NULL DEFAULT 'USD',
            listing_language TEXT NOT NULL DEFAULT 'en',
            category TEXT NOT NULL DEFAULT '',
            image TEXT,
            stock INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            owner TEXT NOT NULL DEFAULT 'admin',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
        CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_token TEXT NOT NULL UNIQUE,
            user_email TEXT NOT NULL,
            full_name TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            total_price REAL NOT NULL,
            total_currency TEXT NOT NULL DEFAULT 'USD',
            fulfillment_status TEXT NOT NULL DEFAULT 'pending',
            tracking_number TEXT,
            carrier_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orders_email ON orders(user_email);
        CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(tracking_token);

        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price_usd REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            path TEXT NOT NULL,
            ip TEXT NOT NULL,
            user_agent TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_visits_ts ON visits(ts);

        CREATE TABLE IF NOT EXISTS app_config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        -- Order timeline: status changes, tracking updates, notes to the
        -- customer and internal admin notes (visible_to_customer = 0).
        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            from_status TEXT,
            to_status TEXT,
            note TEXT,
            visible_to_customer INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id);

        -- Every outgoing email; status is queued/sent/failed, or "logged"
        -- when no SMTP server is configured.
        CREATE TABLE IF NOT EXISTS email_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            to_address TEXT NOT NULL,
            subject TEXT NOT NULL,
            body_text TEXT NOT NULL,
            body_html TEXT,
            kind TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            error TEXT,
            created_at TEXT NOT NULL,
            sent_at TEXT
        );

        -- Discount codes. value is a percentage (kind = percent) or a USD
        -- amount (kind = fixed); max_uses NULL means unlimited.
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            value REAL NOT NULL,
            min_order_usd REAL NOT NULL DEFAULT 0,
            max_uses INTEGER,
            used_count INTEGER NOT NULL DEFAULT 0,
            starts_at TEXT,
            expires_at TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        -- Product reviews: one per customer and product, from verified buyers.
        -- status: published | hidden (moderated by admins).
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            body TEXT NOT NULL,
            reviewer_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'published',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (product_id, user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id, status);

        -- Contact form messages; admins reply by email from the inbox.
        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            subject TEXT NOT NULL,
            message TEXT NOT NULL,
            order_ref TEXT,
            user_id INTEGER,
            locale TEXT NOT NULL DEFAULT 'en',
            status TEXT NOT NULL DEFAULT 'open',
            admin_reply TEXT,
            replied_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_support_status ON support_messages(status);
        CREATE INDEX IF NOT EXISTS idx_support_email ON support_messages(email);

        -- Every admin reply to a support message (the conversation history);
        -- support_messages.admin_reply keeps the latest one.
        CREATE TABLE IF NOT EXISTS support_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id INTEGER NOT NULL REFERENCES support_messages(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_support_replies_message ON support_replies(message_id);

        -- Packages of a listing (e.g. 1K / 5K / 10K followers), each with its own
        -- price (in the listing's base currency) and stock. A listing with at
        -- least one active package is sold per package only.
        CREATE TABLE IF NOT EXISTS product_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            sort_order INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_variants_product ON product_variants(product_id);

        -- Customers' saved (favourite) listings.
        CREATE TABLE IF NOT EXISTS wishlist_items (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, product_id)
        );

        -- "Email me when it is back in stock" requests; notified_at is set once sent.
        CREATE TABLE IF NOT EXISTS stock_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            email TEXT NOT NULL,
            locale TEXT NOT NULL DEFAULT 'en',
            created_at TEXT NOT NULL,
            notified_at TEXT,
            UNIQUE (product_id, email)
        );

        -- Single-use password reset links; only a SHA-256 of the token is stored.
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );

        -- What the customer receives for an order (account details, codes),
        -- shown only on the order's tracking page and never emailed. body is
        -- cleared at purge_at (purged_at set); source is manual or auto.
        CREATE TABLE IF NOT EXISTS order_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            body TEXT,
            source TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            purge_at TEXT NOT NULL,
            purged_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_order_deliveries_order ON order_deliveries(order_id);
        CREATE INDEX IF NOT EXISTS idx_order_deliveries_purge ON order_deliveries(purged_at, purge_at);

        -- Code stock for automatic delivery: codes / licence keys of a listing
        -- (variant_id NULL) or of one of its packages. order_id is set when a
        -- paid order takes the code; code is cleared with that delivery.
        CREATE TABLE IF NOT EXISTS delivery_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            variant_id INTEGER,
            code TEXT,
            created_at TEXT NOT NULL,
            order_id INTEGER REFERENCES orders(id),
            assigned_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_delivery_codes_pool ON delivery_codes(product_id, variant_id, order_id);
        CREATE INDEX IF NOT EXISTS idx_delivery_codes_order ON delivery_codes(order_id);

        -- Security audit log: admin sign-ins, two-step changes, payments,
        -- deliveries, code stock, backups, settings, customer data requests.
        -- detail never holds secrets (passwords, codes, tokens, TOTP secrets);
        -- ip is recorded for admin actions and failed admin sign-ins only.
        CREATE TABLE IF NOT EXISTS security_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            actor TEXT NOT NULL,
            detail TEXT NOT NULL DEFAULT '',
            ip TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_security_events_created ON security_events(created_at);
        """
    )
    # Columns added after the first release; ALTER keeps existing databases working.
    _ensure_column(db, "orders", "user_id", "user_id INTEGER")
    # 1 when the order decremented product stock; orders placed before stock
    # tracking keep 0 so cancelling them does not inflate stock.
    _ensure_column(
        db, "orders", "stock_reserved", "stock_reserved INTEGER NOT NULL DEFAULT 0"
    )
    # UI language at checkout, so order emails reach the customer in that language.
    _ensure_column(db, "orders", "locale", "locale TEXT NOT NULL DEFAULT 'en'")
    # Discount code used at checkout; total_price is already net of discount_usd.
    _ensure_column(db, "orders", "coupon_code", "coupon_code TEXT")
    _ensure_column(db, "orders", "discount_usd", "discount_usd REAL NOT NULL DEFAULT 0")
    # Bumped on password change/reset; sessions carrying an older value are
    # signed out (see services.web.current_user).
    _ensure_column(
        db, "users", "session_version", "session_version INTEGER NOT NULL DEFAULT 0"
    )
    # Package bought (NULL for listings without packages).
    _ensure_column(db, "order_items", "variant_id", "variant_id INTEGER")
    _ensure_column(db, "order_items", "variant_name", "variant_name TEXT")
    # Unpaid orders are cancelled automatically after this moment (UTC ISO).
    # NULL = no deadline: orders placed before the feature, or re-opened by
    # the admin, are never cancelled automatically.
    _ensure_column(db, "orders", "payment_due_at", "payment_due_at TEXT")
    # Set when the admin confirms the payment arrived (NULL = not confirmed;
    # orders from before this column never had it recorded).
    _ensure_column(db, "orders", "paid_at", "paid_at TEXT")
    # The marketplace's own reference for that payment (e.g. itemsatis order no.)
    _ensure_column(db, "orders", "payment_ref", "payment_ref TEXT")
    # When the customer accepted the distance sales agreement / pre-information
    # at checkout, and which version of the texts (services.legal.LEGAL_VERSION)
    _ensure_column(db, "orders", "terms_accepted_at", "terms_accepted_at TEXT")
    _ensure_column(db, "orders", "terms_version", "terms_version TEXT")
    # Optional itemsatis listing for a listing / package; the site-wide link
    # from Admin → Settings is used when empty.
    _ensure_column(db, "products", "payment_url", "payment_url TEXT")
    # Promised delivery time in hours after the payment is confirmed (NULL = not stated)
    _ensure_column(db, "products", "delivery_hours", "delivery_hours INTEGER")
    _ensure_column(db, "product_variants", "payment_url", "payment_url TEXT")
    # What the customer must enter at checkout for this listing (e.g. the
    # Instagram username): JSON list of {label, kind: text|url, required}
    _ensure_column(db, "products", "order_fields", "order_fields TEXT")
    # The customer's answers to those fields for this line: JSON list of
    # {label, value}, labels as they were when the order was placed
    _ensure_column(db, "order_items", "customer_inputs", "customer_inputs TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_orders_payment_due "
        "ON orders(fulfillment_status, payment_due_at)"
    )
    # Replies sent before the history table existed become its first entries
    # (idempotent: only messages without any history row are copied).
    db.execute(
        """
        INSERT INTO support_replies (message_id, body, created_at)
        SELECT m.id, m.admin_reply, COALESCE(m.replied_at, m.created_at)
        FROM support_messages m
        WHERE m.admin_reply IS NOT NULL AND m.admin_reply != ''
          AND NOT EXISTS (SELECT 1 FROM support_replies r WHERE r.message_id = m.id)
        """
    )
    # Line items are always read per order (order pages, emails, CSV export)
    db.execute("CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)")
    # Sales report and date-ordered admin views
    db.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")
    db.commit()


def _ensure_column(db, table: str, column: str, ddl: str) -> None:
    cols = {r["name"] for r in db.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_app(app):
    app.teardown_appcontext(close_db)
