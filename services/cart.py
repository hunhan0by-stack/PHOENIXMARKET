"""
Session cart and priced cart line items.

The cart maps a key to a quantity: "12" for a listing without packages and
"12:3" for package 3 of listing 12.
"""
from flask import session

from services.currency import DEFAULT_CURRENCY, convert
from services.listing import get_base_currency, get_base_price
import services.store as store


def get_cart():
    return session.setdefault("cart", {})


def cart_key(product_id: int, variant_id: int | None = None) -> str:
    return f"{product_id}:{variant_id}" if variant_id else str(product_id)


def parse_cart_key(key):
    """'12' -> (12, None); '12:3' -> (12, 3); malformed -> (None, None)."""
    product_part, _, variant_part = str(key).partition(":")
    try:
        return int(product_part), (int(variant_part) if variant_part else None)
    except ValueError:
        return None, None


def available_stock(product: dict, variant: dict | None = None) -> int:
    """How many units of this listing (or package) can be bought right now."""
    if product.get("status") != "active":
        return 0
    if variant is not None:
        return max(0, int(variant["stock"])) if variant["active"] else 0
    if product.get("has_variants"):
        return 0  # listings with packages are only sold per package
    return max(0, int(product.get("stock") or 0))


def resolve_cart_key(key):
    """(product, variant or None) for a cart key, or (None, None) if it no longer exists."""
    product_id, variant_id = parse_cart_key(key)
    if product_id is None:
        return None, None
    product = store.get_product(product_id)
    if not product:
        return None, None
    if variant_id is None:
        return product, None
    variant = store.get_variant(variant_id)
    if not variant or variant["product_id"] != product_id:
        return None, None
    return product, variant


def build_cart_items(cart_data, display_currency: str = None):
    """
    Return structured cart line items and subtotal.
    Uses display_currency for user-facing prices.
    Order storage uses USD-converted values for consistency.
    """
    cur = display_currency or DEFAULT_CURRENCY
    items = []
    subtotal_usd = 0.0
    subtotal_display = 0.0
    for key, qty in cart_data.items():
        product, variant = resolve_cart_key(key)
        if not product:
            continue
        base_cur = get_base_currency(product)
        base_price = variant["price"] if variant else get_base_price(product)
        line_usd = convert(base_price * qty, base_cur, "USD")
        line_display = convert(base_price * qty, base_cur, cur)
        subtotal_usd += line_usd
        subtotal_display += line_display
        available = available_stock(product, variant)
        items.append({
            "key": key,
            "product": product,
            "variant": variant,
            "name": product["name"] + (f" – {variant['name']}" if variant else ""),
            "qty": qty,
            "unit_price_usd": convert(base_price, base_cur, "USD"),
            "line_total_usd": line_usd,
            "display_unit_price": convert(base_price, base_cur, cur),
            "display_line_total": line_display,
            "available_stock": available,
            "insufficient": qty > available,
        })
    return items, subtotal_usd, subtotal_display
