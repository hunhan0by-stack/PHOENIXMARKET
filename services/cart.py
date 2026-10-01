"""
Session cart and priced cart line items.

The cart maps a key to a quantity: "12" for a listing without packages and
"12:3" for package 3 of listing 12. Prices and stock always come from the
database; the session only ever holds keys and quantities.
"""
from flask import session

from services.currency import DEFAULT_CURRENCY, convert
from services.listing import get_base_currency, get_base_price
import services.store as store

# Upper bound for one cart line, whatever the stock (keeps quantities sane)
MAX_LINE_QUANTITY = 999


class CartLineError(Exception):
    """A requested cart line is not purchasable; .key is a translation key."""

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key = key
        self.params = params


def get_cart():
    return session.setdefault("cart", {})


def save_cart(cart_data: dict) -> None:
    session["cart"] = cart_data


def cart_key(product_id: int, variant_id: int | None = None) -> str:
    return f"{product_id}:{variant_id}" if variant_id else str(product_id)


def parse_cart_key(key):
    """'12' -> (12, None); '12:3' -> (12, 3); malformed -> (None, None)."""
    product_part, _, variant_part = str(key).partition(":")
    try:
        product_id = int(product_part)
        variant_id = int(variant_part) if variant_part else None
    except ValueError:
        return None, None
    if product_id <= 0 or (variant_id is not None and variant_id <= 0):
        return None, None
    return product_id, variant_id


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
    """
    (product, variant or None) for a cart key, or (None, None) when the key no
    longer describes something that exists: unknown listing or package, a
    package of another listing, or a listing that is now sold per package.
    """
    product_id, variant_id = parse_cart_key(key)
    if product_id is None:
        return None, None
    product = store.get_product(product_id)
    if not product:
        return None, None
    if variant_id is None:
        return (None, None) if product["has_variants"] else (product, None)
    variant = store.get_variant(variant_id)
    if not variant or variant["product_id"] != product_id:
        return None, None
    return product, variant


def purchasable_line(product_id: int, variant_raw=None):
    """
    Validate an "add to cart" request: (product, variant or None, available
    stock). Raises CartLineError for anything that cannot be bought: unknown
    or inactive listing, a missing/foreign/inactive package, no stock.
    """
    product = store.get_product(product_id)
    if not product or product.get("status") != "active":
        raise CartLineError("flash_product_unavailable")
    variant = None
    variant_raw = (str(variant_raw).strip() if variant_raw is not None else "")
    if product["has_variants"]:
        if not variant_raw:
            raise CartLineError("flash_choose_package")
        try:
            variant = store.get_variant(int(variant_raw))
        except ValueError:
            variant = None
        if not variant or variant["product_id"] != product["id"] or not variant["active"]:
            raise CartLineError("flash_package_unavailable")
    elif variant_raw:
        # A package id for a listing without packages is a stale or forged form
        raise CartLineError("flash_package_unavailable")
    available = available_stock(product, variant)
    if available <= 0:
        raise CartLineError("flash_out_of_stock", name=line_name(product, variant))
    return product, variant, available


def line_name(product: dict, variant: dict | None) -> str:
    return product["name"] + (f" – {variant['name']}" if variant else "")


def unit_price(product: dict, variant: dict | None) -> float:
    """Price of one unit in the listing's base currency (package price when chosen)."""
    return float(variant["price"]) if variant else get_base_price(product)


def build_cart_items(cart_data, display_currency: str = None):
    """
    Return structured cart line items and subtotal.
    Uses display_currency for user-facing prices.
    Order storage uses USD-converted values for consistency.
    Keys that no longer resolve (deleted listing/package, listing now sold
    per package) are left out; prune_cart() drops them from the session.
    """
    cur = display_currency or DEFAULT_CURRENCY
    items = []
    subtotal_usd = 0.0
    subtotal_display = 0.0
    for key, qty in cart_data.items():
        product, variant = resolve_cart_key(key)
        if not product:
            continue
        qty = int(qty)
        base_cur = get_base_currency(product)
        price = unit_price(product, variant)
        unit_usd = round(convert(price, base_cur, "USD"), 2)
        unit_display = round(convert(price, base_cur, cur), 2)
        line_usd = round(unit_usd * qty, 2)
        line_display = round(unit_display * qty, 2)
        subtotal_usd += line_usd
        subtotal_display += line_display
        available = available_stock(product, variant)
        items.append({
            "key": key,
            "product": product,
            "variant": variant,
            "name": line_name(product, variant),
            "qty": qty,
            "unit_price_usd": unit_usd,
            "line_total_usd": line_usd,
            "display_unit_price": unit_display,
            "display_line_total": line_display,
            "available_stock": available,
            "insufficient": qty > available,
        })
    return items, round(subtotal_usd, 2), round(subtotal_display, 2)


def prune_cart(cart_data: dict, items: list) -> None:
    """Remove session keys that build_cart_items could not resolve."""
    valid = {item["key"] for item in items}
    stale = [key for key in cart_data if key not in valid]
    if stale:
        for key in stale:
            cart_data.pop(key, None)
        save_cart(cart_data)
