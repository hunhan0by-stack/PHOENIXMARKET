"""Each language has a crawlable address (?lang=), hreflang alternates and sitemap entries."""
import re

import pytest

from app import app

BASE = "http://localhost"


@pytest.fixture
def default_tr():
    previous = app.config["DEFAULT_LOCALE"]
    app.config["DEFAULT_LOCALE"] = "tr"
    yield
    app.config["DEFAULT_LOCALE"] = previous


def head_links(html):
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', html)
    alternates = dict(re.findall(r'<link rel="alternate" hreflang="([^"]+)" href="([^"]+)"', html))
    return (canonical.group(1) if canonical else None), alternates


def test_shop_is_turkish_first_without_configuration(make_client, monkeypatch):
    """No DEFAULT_LOCALE (or an unknown one): first-time visitors and crawlers get Turkish."""
    from services.web import default_locale

    for value in (None, "", "xx"):
        monkeypatch.setitem(app.config, "DEFAULT_LOCALE", value)
        with app.app_context():
            assert default_locale() == "tr"
    monkeypatch.setitem(app.config, "DEFAULT_LOCALE", None)
    assert '<html lang="tr">' in make_client().html("/")


def test_render_blueprint_sets_turkish_default():
    from pathlib import Path

    blueprint = (Path(app.root_path) / "render.yaml").read_text(encoding="utf-8")
    assert re.search(r"- key: DEFAULT_LOCALE\s+value: tr", blueprint)


def test_search_engines_can_reach_the_turkish_pages(make_client):
    crawler = make_client()  # no cookies, like a search engine
    assert '<html lang="en">' in crawler.html("/products")
    tr = make_client(ip="10.0.0.9").html("/products?lang=tr")
    assert '<html lang="tr">' in tr and "Dijital hizmetler pazaryeri" in tr


def test_url_language_is_remembered(client):
    client.get("/products?lang=tr")
    assert '<html lang="tr">' in client.html("/contact")
    client.get("/?lang=xx")  # unknown values are ignored
    assert '<html lang="tr">' in client.html("/")


def test_canonical_and_hreflang_with_english_default(client):
    canonical, alt = head_links(client.html("/products"))
    assert canonical == f"{BASE}/products"
    assert alt == {"en": f"{BASE}/products", "tr": f"{BASE}/products?lang=tr", "x-default": f"{BASE}/products"}
    canonical, _ = head_links(client.html("/products?lang=tr&sort=price_asc"))
    assert canonical == f"{BASE}/products?lang=tr"


def test_turkish_default_flips_the_addresses(make_client, default_tr):
    visitor = make_client()
    html = visitor.html("/products")
    assert '<html lang="tr">' in html
    canonical, alt = head_links(html)
    assert canonical == f"{BASE}/products"
    assert alt["en"] == f"{BASE}/products?lang=en" and alt["tr"] == f"{BASE}/products"


def test_private_pages_have_no_language_alternates(client):
    html = client.html("/cart")
    assert 'name="robots" content="noindex, nofollow"' in html
    assert head_links(html) == (None, {})


def test_language_switch_links_override_a_saved_choice(client):
    client.get("/?lang=tr")
    html = client.html("/products?sort=price_asc")
    assert 'href="http://localhost/products?sort=price_asc&amp;lang=en"' in html
    client.get("/products?sort=price_asc&lang=en")
    assert '<html lang="en">' in client.html("/products")


def test_sitemap_lists_every_page_in_both_languages(client):
    xml = client.get("/sitemap.xml").get_data(as_text=True)
    locs = re.findall(r"<loc>([^<]+)</loc>", xml)
    assert f"{BASE}/products" in locs and f"{BASE}/products?lang=tr" in locs
    assert len(locs) == 2 * len({loc.split("?")[0] for loc in locs})
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
    assert f'<xhtml:link rel="alternate" hreflang="tr" href="{BASE}/legal/privacy?lang=tr"/>' in xml


def test_old_set_locale_links_still_work(client):
    r = client.get("/set-locale/tr", headers={"Referer": "http://localhost/products"})
    assert r.status_code == 302
    assert '<html lang="tr">' in client.html("/")
