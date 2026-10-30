"""
Where a customer pays for an order. Payment happens on itemsatis: a package
or listing can have its own itemsatis listing (Admin → listing form); the
site-wide link from Admin → Settings is used for everything else.
"""
from flask import current_app

import services.store as store
from services.itemsatis import is_valid_link, link_problem


def _valid_or_none(url):
    return url if is_valid_link(url) else None


def payment_links(order: dict) -> list:
    """Distinct payment links for an order's lines, in line order: [{url, names}].

    Uses the current listing/package links, so a corrected link also reaches
    customers who ordered before the correction. A saved link that fails the
    itemsatis check (e.g. saved before the check existed) is skipped like an
    empty one; the record itself is left as it is.
    """
    default_url = _valid_or_none(current_app.config["ITEMSATIS_REDIRECT_URL"])
    links = {}
    for item in order.get("line_items") or []:
        url = None
        if item.get("variant_id"):
            variant = store.get_variant(item["variant_id"])
            url = _valid_or_none(variant.get("payment_url")) if variant else None
        if not url:
            product = store.get_product(item["product_id"])
            url = _valid_or_none(product.get("payment_url")) if product else None
        url = url or default_url
        if url:
            links.setdefault(url, []).append(item["display_name"])
    if not links and default_url:
        links[default_url] = []
    return [{"url": url, "names": names} for url, names in links.items()]


def saved_link_problems() -> list:
    """Saved payment links that fail the itemsatis check: [{where, url, problem}]. Read-only."""
    found = []
    site_link = store.get_config("site_settings.itemsatis_redirect_url", None)
    if site_link and link_problem(site_link):
        found.append({"where": "Site-wide link (Admin → Settings)", "url": site_link, "problem": link_problem(site_link)})
    for product in store.list_all_products():
        if product.get("payment_url") and link_problem(product["payment_url"]):
            found.append({
                "where": f"Listing #{product['id']} {product['name']}",
                "url": product["payment_url"], "problem": link_problem(product["payment_url"]),
            })
        for variant in store.list_variants(product["id"]):
            if variant.get("payment_url") and link_problem(variant["payment_url"]):
                found.append({
                    "where": f"Package #{variant['id']} {variant['name']} (listing #{product['id']})",
                    "url": variant["payment_url"], "problem": link_problem(variant["payment_url"]),
                })
    return found
