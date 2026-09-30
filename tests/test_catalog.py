"""Catalog sorting/filtering/pagination, search, related products and SEO tags."""
import json
import re

import pytest

import services.store as store
from tests.helpers import in_app, set_stock

SEEDED = ["Canva Pro Subscription", "TikTok Views", "Instagram Followers", "ChatGPT Accounts"]  # by price


def add_product(name, price=5.0, category="Design", stock=10):
    existing = in_app(store.list_existing_slugs)
    return in_app(store.create_product, {
        "name": name, "slug": store.unique_slug(name, existing), "description": f"{name} description",
        "base_price": price, "base_currency": "USD", "listing_language": "en",
        "category": category, "image_path": "", "stock": stock, "status": "active",
    })


def names_in_order(html, names):
    positions = {n: html.find(f">{n}</a>") for n in names}
    assert all(p >= 0 for p in positions.values()), positions
    return sorted(names, key=positions.get)


def test_sort_by_price(client):
    assert names_in_order(client.html("/products?sort=price_asc"), SEEDED) == SEEDED
    assert names_in_order(client.html("/products?sort=price_desc"), SEEDED) == SEEDED[::-1]
    featured = ["Canva Pro Subscription", "Instagram Followers", "TikTok Views", "ChatGPT Accounts"]
    assert names_in_order(client.html("/products?sort=bogus"), SEEDED) == featured  # unknown -> featured


def test_in_stock_filter_and_result_count(client):
    set_stock(1, 0)
    html = client.html("/products?in_stock=1")
    assert "Canva Pro Subscription" not in html and "3 result(s)" in html
    assert "4 result(s)" in client.html("/products")


def test_catalog_pagination_keeps_filters(client):
    for i in range(10):
        add_product(f"Extra Service {i:02d}")
    page1 = client.html("/products?sort=newest")
    assert 'aria-label="Pagination"' in page1
    assert "page=2" in page1 and "sort=newest" in page1
    page2 = client.html("/products?sort=newest&page=2")
    assert page2.count("<article") == 2


def test_category_pagination_links_keep_the_slug(client):
    for i in range(13):
        add_product(f"Design Pack {i:02d}")
    html = client.html("/category/design")
    assert re.search(r'href="/category/design\?page=2"', html)


def test_search_matches_all_words_in_any_order(client):
    assert "TikTok Views" in client.html("/search?q=views+tiktok")
    assert "No products matched" in client.html("/search?q=canva+views")


@pytest.mark.parametrize("query", ["instagram takipçi", "INSTAGRAM TAKİPÇİ", "ınstagram"])
def test_search_handles_turkish_case(client, query):
    add_product("İNSTAGRAM TAKİPÇİ PAKETİ", category="Social Media")
    assert "İNSTAGRAM TAKİPÇİ PAKETİ" in client.html(f"/search?q={query}")


def test_related_products(client):
    add_product("Canva Templates", category="Design")
    html = client.html("/product/canva-pro-subscription")
    related = html.split("You may also like", 1)[1]
    assert "Canva Templates" in related and related.index("Canva Templates") < related.index("TikTok Views")


def test_product_seo_tags(client):
    html = client.html("/product/tiktok-views")
    assert '<meta property="og:type" content="product"' in html
    assert '<link rel="canonical" href="http://localhost/product/tiktok-views"' in html
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
    assert data["@type"] == "Product" and data["offers"]["price"] == "12.99"
    assert data["offers"]["availability"].endswith("InStock")
    assert data["image"].startswith("http://localhost/static/")


def test_robots_sitemap_and_noindex(client, admin):
    robots = client.get("/robots.txt").get_data(as_text=True)
    assert "Disallow: /admin/" in robots and "Sitemap: http://localhost/sitemap.xml" in robots
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    assert "<lastmod>" in sitemap and "/product/tiktok-views" in sitemap
    assert 'name="robots" content="noindex' in admin.html("/admin/dashboard")
    assert 'name="robots" content="noindex' not in client.html("/products")
