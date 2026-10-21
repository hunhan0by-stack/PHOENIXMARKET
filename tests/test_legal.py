"""Legal pages, seller details, checkout consent and the dashboard reminder."""
import pytest

import services.legal as legal
import services.store as store
from tests.helpers import in_app

PAGES = ["privacy", "distance-sales", "returns", "cookies"]
SELLER = {"seller_name": "Örnek Dijital Ltd. Şti.", "seller_address": "Atatürk Cad. 1, İstanbul",
          "seller_tax_id": "Kadıköy VD 1234567890", "seller_phone": ""}


def owner_email(admin, email="destek@ornek.com"):
    admin.post("/admin/settings", {"itemsatis_redirect_url": "", "support_email": email, "support_discord": ""})


@pytest.mark.parametrize("page", PAGES)
@pytest.mark.parametrize("locale", ["en", "tr"])
def test_legal_pages_render_in_both_languages(make_client, page, locale):
    r = make_client(locale=locale).get(f"/legal/{page}")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert f"legal_{page}_title" not in html  # translated
    assert legal.LEGAL_VERSION in html


def test_unknown_legal_page_is_404(client):
    assert client.get("/legal/terms-of-everything").status_code == 404


def test_missing_seller_details_are_visible_not_invented(make_client):
    html = make_client(locale="tr").html("/legal/distance-sales")
    assert "[henüz girilmedi]" in html


def test_seller_details_from_settings_appear_on_the_pages(admin, make_client):
    owner_email(admin)
    admin.post("/admin/settings/legal", SELLER)
    html = make_client(locale="tr").html("/legal/privacy")
    assert "Örnek Dijital Ltd. Şti." in html and "Kadıköy VD 1234567890" in html
    assert "destek@ornek.com" in html
    assert "[henüz girilmedi]" not in html
    assert in_app(legal.missing_seller_fields) == []


def test_pages_describe_what_the_site_really_does(make_client):
    tr = make_client(locale="tr")
    assert "24 saat" in tr.html("/legal/distance-sales")  # PENDING_ORDER_TTL_HOURS
    assert "90 gün" in tr.html("/legal/cookies")
    # The AI chat transfer is only mentioned when the chat is enabled
    assert "OpenAI" not in tr.html("/legal/privacy")


def test_checkout_requires_consent_and_records_it(client):
    client.post("/cart/add/2")
    r = client.post("/checkout", {"full_name": "A", "email": "a@x.com", "accept_terms": ""})
    body = r.get_data(as_text=True)
    assert r.status_code == 200  # the form is shown again, input kept
    assert 'value="a@x.com"' in body
    assert "Please read and accept the distance sales agreement" in body
    assert in_app(store.count_orders) == 0
    client.post("/checkout", {"full_name": "A", "email": "a@x.com"})
    order = in_app(store.get_order_by_id, in_app(store.list_orders_admin)[0]["id"])
    assert order["terms_version"] == legal.LEGAL_VERSION and order["terms_accepted_at"] is not None


def test_checkout_form_links_the_texts(client):
    client.post("/cart/add/2")
    html = client.html("/checkout")
    assert 'name="accept_terms"' in html and "required" in html.split('name="accept_terms"', 1)[1][:40]
    assert 'href="/legal/distance-sales"' in html and 'href="/legal/privacy"' in html


def test_footer_and_sitemap_list_the_pages(client):
    html = client.html("/")
    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    for page in PAGES:
        assert f'href="/legal/{page}"' in html
        assert f"/legal/{page}</loc>" in sitemap


def test_dashboard_reminds_until_details_and_review_are_done(admin):
    html = admin.html("/admin/dashboard")
    assert "Legal pages: missing" in html and "have not been marked as reviewed" in html
    owner_email(admin)
    admin.post("/admin/settings/legal", {**SELLER, "texts_reviewed": "1"})
    html = admin.html("/admin/dashboard")
    assert "Legal pages: missing" not in html and "marked as reviewed" not in html
    assert in_app(legal.texts_reviewed)


def test_new_text_version_needs_a_new_review(admin, monkeypatch):
    owner_email(admin)
    admin.post("/admin/settings/legal", {**SELLER, "texts_reviewed": "1"})
    monkeypatch.setattr(legal, "LEGAL_VERSION", "2099-01-01")
    assert not in_app(legal.texts_reviewed)


def test_admin_order_page_shows_the_consent(client, admin):
    client.post("/cart/add/2")
    client.post("/checkout", {"full_name": "A", "email": "a@x.com"})
    order_id = in_app(store.list_orders_admin)[0]["id"]
    html = admin.html(f"/admin/orders/{order_id}")
    assert "Terms accepted:" in html and f"version {legal.LEGAL_VERSION}" in html


def test_legal_settings_are_admin_only(client):
    r = client.post("/admin/settings/legal", SELLER)
    assert r.headers["Location"].endswith("/admin/login")
    assert in_app(legal.seller_info)["name"] == ""
