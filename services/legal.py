"""
Legal pages (privacy / KVKK notice, distance sales agreement, returns and
delivery, cookies) and the seller details they are built from.

The texts in templates/legal/ are templates, not legal advice: the owner
fills in the seller details (Admin → Settings) and has the texts reviewed;
the dashboard reminds them until both are done. LEGAL_VERSION is stored on
every order with the time the customer accepted the terms, so it must be
bumped whenever the texts change.
"""
import services.store as store

LEGAL_VERSION = "2026-10-22"

PAGES = ("privacy", "distance-sales", "returns", "cookies")

SELLER_FIELDS = ("name", "address", "tax_id", "phone")
# Without these the agreement does not identify the seller
REQUIRED_SELLER_FIELDS = ("name", "address", "tax_id")
SELLER_MAX_LENGTH = 300
REVIEWED_KEY = "legal.reviewed_version"


def seller_info() -> dict:
    info = {field: store.get_config(f"seller.{field}") or "" for field in SELLER_FIELDS}
    email = (store.get_config("site_settings.support_email") or "").strip()
    info["email"] = "" if email.endswith(".example") else email
    return info


def save_seller_info(values: dict) -> None:
    for field in SELLER_FIELDS:
        if field in values:
            store.set_config(f"seller.{field}", (values[field] or "").strip()[:SELLER_MAX_LENGTH])


def missing_seller_fields() -> list:
    info = seller_info()
    missing = [field for field in REQUIRED_SELLER_FIELDS if not info[field]]
    if not info["email"]:
        missing.append("email")
    return missing


def texts_reviewed() -> bool:
    """The owner confirmed this version of the texts was reviewed."""
    return store.get_config(REVIEWED_KEY) == LEGAL_VERSION


def set_texts_reviewed(reviewed: bool) -> None:
    store.set_config(REVIEWED_KEY, LEGAL_VERSION if reviewed else "")
