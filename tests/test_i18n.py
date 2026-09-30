"""Turkish/English UI: translated pages, flashes and no leaked translation keys."""
import re

import pytest

from services.translations import TRANSLATIONS

PUBLIC_PAGES = [
    "/", "/products", "/search?q=zzz", "/category/design", "/product/tiktok-views",
    "/cart", "/signin", "/signup", "/contact", "/forgot-password", "/nope",
]


def test_locales_define_the_same_keys():
    assert set(TRANSLATIONS["en"]) == set(TRANSLATIONS["tr"])


@pytest.mark.parametrize(
    "path,needles",
    [
        ("/", ["Neden biz", "Pazaryeri", "Sepete ekle"]),
        ("/products", ["Dijital hizmetler pazaryeri"]),
        ("/signin", ["Müşteri girişi"]),
        ("/signup", ["PHOENIXMARKET&#39;e katılın"]),  # apostrophe is HTML-escaped
        ("/forgot-password", ["Şifrenizi mi unuttunuz?"]),
        ("/nope-404", ["Aradığınız sayfayı bulamadık"]),
    ],
)
def test_turkish_pages(make_client, path, needles):
    html = make_client(locale="tr").html(path)
    assert all(n in html for n in needles)


def test_turkish_flash_message(make_client):
    c = make_client(locale="tr")
    c.post("/cart/add/2")
    assert any("Sepete eklendi" in m for m in c.flashes())


@pytest.mark.parametrize("locale", ["en", "tr"])
def test_no_raw_translation_keys_are_rendered(make_client, locale):
    # Keys like "orders"/"password" are also plain words; only underscore keys can leak visibly.
    keys = {k for k in TRANSLATIONS["en"] if "_" in k}
    c = make_client(locale=locale)
    c.post("/cart/add/2")
    leaks = set()
    for path in PUBLIC_PAGES + ["/checkout"]:
        visible = re.sub(r"<[^>]+>", " ", c.html(path))
        leaks |= {k for k in keys if re.search(r"(?<![\w/.-])" + re.escape(k) + r"(?![\w/.-])", visible)}
    assert not leaks
