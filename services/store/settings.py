"""
Key/value configuration: editable site texts (EN/TR), site settings, first-run seed.
"""
from datetime import datetime, timezone

from services.database import get_db
from services.store.base import unique_slug
from services.store.catalog import category_slugs_excluding


DEFAULT_SITE_CONTENT = {
    "hero_badge": "Fast & reliable digital services",
    "hero_title": "PHOENIXMARKET",
    "hero_subtitle": (
        "A modern marketplace for digital services like Canva Pro subscriptions, "
        "Instagram followers, TikTok views, and ChatGPT accounts. "
        "Affordable pricing, fast delivery, and trusted support."
    ),
    "hero_cta_label": "Browse services",
    "checkout_helper_text": (
        "To complete this purchase you will be redirected to our official marketplace listing on itemsatis."
    ),
    "checkout_button_label": "Continue to secure purchase",
    "footer_contact_text": "contact@phoenixmarket.example",
    "footer_discord_text": "YourDiscordTag#0000",
}


SITE_CONTENT_KEYS = list(DEFAULT_SITE_CONTENT.keys())


# Texts that depend on the visitor's language get a Turkish version
# (site_content.<key>.tr); the brand name and contact details do not.
TRANSLATABLE_SITE_CONTENT_KEYS = (
    "hero_badge", "hero_subtitle", "hero_cta_label", "checkout_helper_text", "checkout_button_label",
)


DEFAULT_SITE_CONTENT_TR = {
    "hero_badge": "Hızlı ve güvenilir dijital hizmetler",
    "hero_subtitle": (
        "Canva Pro abonelikleri, Instagram takipçileri, TikTok izlenmeleri ve ChatGPT hesapları "
        "gibi dijital hizmetler için modern bir pazaryeri. Uygun fiyat, hızlı teslimat ve "
        "güvenilir destek."
    ),
    "hero_cta_label": "Hizmetlere göz at",
    "checkout_helper_text": (
        "Satın alımı tamamlamak için itemsatis'teki resmi mağaza ilanımıza yönlendirileceksiniz."
    ),
    "checkout_button_label": "Güvenli ödemeye devam et",
}


def get_config(key: str, default=None):
    db = get_db()
    row = db.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return row["value"]


def set_config(key: str, value: str) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO app_config (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    db.commit()


def load_site_content(locale: str = "en") -> dict:
    """Editable site texts in the visitor's language.

    Turkish: the owner's Turkish text; without one, the built-in Turkish text
    while the English one is still the default, else the owner's own
    (English) text rather than a generic one that may contradict it.
    """
    stored = {
        row["key"][len("site_content."):]: row["value"]
        for row in get_db().execute("SELECT key, value FROM app_config WHERE key LIKE 'site_content.%'")
        if row["value"] is not None
    }
    out = {key: stored.get(key, default) for key, default in DEFAULT_SITE_CONTENT.items()}
    if locale == "tr":
        for key in TRANSLATABLE_SITE_CONTENT_KEYS:
            if stored.get(f"{key}.tr"):
                out[key] = stored[f"{key}.tr"]
            elif out[key] == DEFAULT_SITE_CONTENT[key]:
                out[key] = DEFAULT_SITE_CONTENT_TR[key]
    return out


def load_site_content_tr_overrides() -> dict:
    """The owner's own Turkish texts only (empty when the built-in one is used)."""
    return {
        key: get_config(f"site_content.{key}.tr") or ""
        for key in TRANSLATABLE_SITE_CONTENT_KEYS
    }


def save_site_content(updates: dict, tr_updates: dict | None = None) -> None:
    for key, val in updates.items():
        if key in SITE_CONTENT_KEYS:
            set_config(f"site_content.{key}", val)
    db = get_db()
    for key, val in (tr_updates or {}).items():
        if key not in TRANSLATABLE_SITE_CONTENT_KEYS:
            continue
        if val:
            set_config(f"site_content.{key}.tr", val)
        else:  # empty: back to the built-in Turkish text
            db.execute("DELETE FROM app_config WHERE key = ?", (f"site_content.{key}.tr",))
            db.commit()


def load_site_settings(default_itemsatis: str, default_email: str, default_discord: str) -> dict:
    return {
        "itemsatis_redirect_url": get_config(
            "site_settings.itemsatis_redirect_url", default_itemsatis
        ),
        "support_email": get_config(
            "site_settings.support_email", default_email
        ),
        "support_discord": get_config(
            "site_settings.support_discord", default_discord
        ),
        # Language of the owner's own notification emails
        "owner_locale": get_config("site_settings.owner_locale", "en"),
    }


def save_site_settings(
    itemsatis_url: str, support_email: str, support_discord: str, owner_locale: str | None = None
) -> None:
    if itemsatis_url:
        set_config("site_settings.itemsatis_redirect_url", itemsatis_url)
    set_config("site_settings.support_email", support_email)
    set_config("site_settings.support_discord", support_discord)
    if owner_locale:
        set_config("site_settings.owner_locale", owner_locale)


def migrate_listing_images_png_to_jpg() -> None:
    """Point product posters back to canonical .jpg assets (idempotent)."""
    db = get_db()
    pairs = (
        ("/static/images/canva.png", "/static/images/canva.jpg"),
        ("/static/images/instagram.png", "/static/images/instagram.jpg"),
        ("/static/images/tiktok.png", "/static/images/tiktok.jpg"),
        ("/static/images/chatgpt.png", "/static/images/chatgpt.jpg"),
    )
    for old, new in pairs:
        db.execute("UPDATE products SET image = ? WHERE image = ?", (new, old))
    db.commit()


def seed_if_empty(default_itemsatis: str) -> None:
    db = get_db()
    n = db.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    if n > 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    seed_products = [
        {
            "name": "Canva Pro Subscription",
            "slug": "canva-pro-subscription",
            "base_price": 9.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Premium Canva Pro access with templates and collaboration tools.",
            "category": "Design",
            "image": "/static/images/canva.jpg",
            "stock": 100,
            "status": "active",
            "owner": "admin",
        },
        {
            "name": "Instagram Followers",
            "slug": "instagram-followers",
            "base_price": 14.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Boost your Instagram presence with targeted follower packages.",
            "category": "Social Media",
            "image": "/static/images/instagram.jpg",
            "stock": 200,
            "status": "active",
            "owner": "admin",
        },
        {
            "name": "TikTok Views",
            "slug": "tiktok-views",
            "base_price": 12.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Increase your TikTok views for better reach and engagement.",
            "category": "Social Media",
            "image": "/static/images/tiktok.jpg",
            "stock": 150,
            "status": "active",
            "owner": "admin",
        },
        {
            "name": "ChatGPT Accounts",
            "slug": "chatgpt-accounts",
            "base_price": 19.99,
            "base_currency": "USD",
            "listing_language": "en",
            "description": "Reliable ChatGPT access for research, coding and content.",
            "category": "AI",
            "image": "/static/images/chatgpt.jpg",
            "stock": 80,
            "status": "active",
            "owner": "admin",
        },
    ]
    for p in seed_products:
        db.execute(
            """
            INSERT INTO products (
                name, slug, description, base_price, base_currency, listing_language,
                category, image, stock, status, owner, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                p["name"],
                p["slug"],
                p["description"],
                p["base_price"],
                p["base_currency"],
                p["listing_language"],
                p["category"],
                p["image"],
                p["stock"],
                p["status"],
                p["owner"],
                now,
                now,
            ),
        )
    seen = set()
    for p in seed_products:
        name = p["category"]
        if not name or name in seen:
            continue
        seen.add(name)
        slug = unique_slug(name, category_slugs_excluding())
        db.execute(
            """
            INSERT INTO categories (name, slug, parent_id, created_at)
            VALUES (?, ?, NULL, ?)
            """,
            (name, slug, now),
        )
    for key, val in DEFAULT_SITE_CONTENT.items():
        db.execute(
            "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
            (f"site_content.{key}", val),
        )
    db.execute(
        "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
        ("site_settings.itemsatis_redirect_url", default_itemsatis),
    )
    db.execute(
        "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
        ("site_settings.support_email", "contact@phoenixmarket.example"),
    )
    db.execute(
        "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
        ("site_settings.support_discord", "YourDiscordTag#0000"),
    )
    db.commit()
