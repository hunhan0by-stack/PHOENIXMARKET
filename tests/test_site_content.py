"""Editable site texts in the visitor's language (Admin → Content)."""
import services.store as store
from tests.helpers import in_app

TR_BUTTON = store.DEFAULT_SITE_CONTENT_TR["checkout_button_label"]
EN_BUTTON = store.DEFAULT_SITE_CONTENT["checkout_button_label"]


def content_form(**over):
    data = dict(store.DEFAULT_SITE_CONTENT)
    data.update(over)
    return data


def test_turkish_visitors_get_turkish_texts_by_default(make_client):
    # Like the live database: the English defaults are stored explicitly
    in_app(store.save_site_content, dict(store.DEFAULT_SITE_CONTENT))
    tr = make_client(locale="tr").html("/")
    en = make_client(ip="10.0.0.2").html("/")
    assert store.DEFAULT_SITE_CONTENT_TR["hero_cta_label"] in tr
    assert store.DEFAULT_SITE_CONTENT["hero_cta_label"] in en
    assert store.DEFAULT_SITE_CONTENT["hero_cta_label"] not in tr


def test_owner_turkish_text_wins_for_turkish_visitors(admin, make_client):
    admin.post("/admin/content", content_form(checkout_button_label__tr="itemsatis'te öde"))
    with_cart = make_client(locale="tr")
    with_cart.post("/cart/add/2")
    assert "itemsatis&#39;te öde" in with_cart.html("/checkout")
    en = make_client(ip="10.0.0.2")
    en.post("/cart/add/2")
    assert EN_BUTTON in en.html("/checkout")


def test_custom_english_text_is_kept_when_no_turkish_one_exists(admin, make_client):
    admin.post("/admin/content", content_form(hero_cta_label="See all deals"))
    assert "See all deals" in make_client(locale="tr").html("/")


def test_clearing_the_turkish_text_restores_the_built_in_one(admin):
    admin.post("/admin/content", content_form(hero_badge__tr="Özel rozet"))
    assert in_app(store.load_site_content, "tr")["hero_badge"] == "Özel rozet"
    admin.post("/admin/content", content_form(hero_badge__tr=""))
    assert in_app(store.load_site_content, "tr")["hero_badge"] == store.DEFAULT_SITE_CONTENT_TR["hero_badge"]


def test_contact_details_are_the_same_in_both_languages(admin, make_client):
    admin.post("/admin/content", content_form(footer_contact_text="destek@ornek.com"))
    assert "destek@ornek.com" in make_client(locale="tr").html("/")
    assert "destek@ornek.com" in make_client(ip="10.0.0.2").html("/")


def test_admin_form_shows_each_language_in_its_own_field(make_client):
    admin = make_client(locale="tr", ip="10.9.9.7")
    admin.login_admin()
    admin.post("/admin/content", content_form(hero_cta_label__tr="Keşfet"))
    html = admin.html("/admin/content")
    # English field keeps the English text even though the admin UI is Turkish
    assert f'name="hero_cta_label" value="{store.DEFAULT_SITE_CONTENT["hero_cta_label"]}"' in html
    assert 'name="hero_cta_label__tr" value="Keşfet"' in html
    assert f'placeholder="{store.DEFAULT_SITE_CONTENT_TR["hero_badge"]}"' in html
