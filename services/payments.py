"""
Where a customer pays for an order. Payment happens on itemsatis: a package
or listing can have its own itemsatis listing (Admin → listing form); the
site-wide link from Admin → Settings is used for everything else.
"""
from flask import current_app

import services.store as store


def payment_links(order: dict) -> list:
    """Distinct payment links for an order's lines, in line order: [{url, names}].

    Uses the current listing/package links, so a corrected link also reaches
    customers who ordered before the correction.
    """
    default_url = current_app.config["ITEMSATIS_REDIRECT_URL"]
    links = {}
    for item in order.get("line_items") or []:
        url = None
        if item.get("variant_id"):
            variant = store.get_variant(item["variant_id"])
            url = variant.get("payment_url") if variant else None
        if not url:
            product = store.get_product(item["product_id"])
            url = product.get("payment_url") if product else None
        links.setdefault(url or default_url, []).append(item["display_name"])
    if not links:
        links[default_url] = []
    return [{"url": url, "names": names} for url, names in links.items()]
