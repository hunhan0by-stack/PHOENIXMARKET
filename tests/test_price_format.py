"""Turkish number formatting for prices (display only: amounts, currencies and calculations unchanged)."""
import re

import pytest

import services.store as store
from services.currency import format_amount, format_price, format_usd
from tests.helpers import in_app

CANVA = 1  # 9.99 USD in the seeded catalog
TR_NUMBER = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}")


@pytest.mark.parametrize("amount, tr, en", [
    (1234.5, "1.234,50", "1234.50"),
    (0, "0,00", "0.00"),
    (9.99, "9,99", "9.99"),
    (1234567.891, "1.234.567,89", "1234567.89"),
    (-1234.5, "-1.234,50", "-1234.50"),
    (None, "0,00", "0.00"),
])
def test_format_amount(amount, tr, en):
    assert format_amount(amount, "tr") == tr
    assert format_amount(amount, "en") == en


def test_format_price_turkish_and_unchanged_english():
    assert format_price(1234.5, "TRY", "tr") == "1.234,50 ₺"
    assert format_price(1234.5, "USD", "tr") == "1.234,50 $"
    assert format_price(1234.5, "EUR", "tr") == "1.234,50 €"
    # English output is exactly what it was before
    assert format_price(1234.5, "TRY", "en") == "1 234.50 ₺"
    assert format_price(9.99, "USD", "en") == "9.99 $"


def test_format_usd_turkish_and_unchanged_english():
    assert format_usd(1234.5, "tr") == "1.234,50 $"
    assert format_usd(1234.5, "en") == "$1234.50"
    assert format_usd(9.99, "en") == "$9.99"


def test_outside_a_request_the_default_is_english():
    assert format_price(1234.5, "TRY") == "1 234.50 ₺"


def place(client, email):
    client.post(f"/cart/add/{CANVA}")
    client.post("/checkout", {"full_name": "Alıcı", "email": email})
    return in_app(store.list_orders_admin)[0]


def test_turkish_pages_show_turkish_numbers(make_client):
    c = make_client(locale="tr", ip="10.130.0.1")
    c.get("/set-currency/TRY")
    c.post(f"/cart/add/{CANVA}")
    cart = c.html("/cart")
    assert re.search(TR_NUMBER.pattern + " ₺", cart)
    assert not re.search(r"\d \d{3}\.\d{2} ₺|\d\.\d{2} ₺", cart)  # no English-style amounts left
    c.get("/set-currency/USD")
    assert "9,99 $" in c.html("/cart")


def test_order_pages_and_emails_follow_the_order_language(make_client, admin):
    tr = make_client(locale="tr", ip="10.130.0.2")
    tr.signup(email="tr@x.test", password="uzunsifre1")
    order_tr = place(tr, "tr@x.test")
    assert "1 × 9,99 $" in tr.html(f"/track/{order_tr['tracking_token']}")
    history = tr.html("/account/orders")
    assert "1 × 9,99 $" in history and "$9.99" not in history
    en = make_client(locale="en", ip="10.130.0.3")
    order_en = place(en, "en@x.test")
    assert "1 × $9.99" in en.html(f"/track/{order_en['tracking_token']}")

    emails = {e["to_address"]: e for e in in_app(store.list_emails) if e["kind"] == "order_confirmation"}
    assert "1 x 9,99 $" in emails["tr@x.test"]["body_text"] and "9,99 $" in emails["tr@x.test"]["body_html"]
    assert "1 x $9.99" in emails["en@x.test"]["body_text"]


def test_owner_email_subject_in_turkish(client, admin):
    in_app(store.set_config, "site_settings.support_email", "owner@shop.test")
    in_app(store.set_config, "site_settings.owner_locale", "tr")
    place(client, "buyer@x.test")
    [owner] = [e for e in in_app(store.list_emails) if e["kind"] == "owner_new_order"]
    assert owner["subject"].endswith("– 9,99 $") and "9,99 $" in owner["body_text"]


def test_admin_in_turkish_keeps_number_inputs_machine_readable(make_client, admin):
    variant_id = in_app(store.create_variant, CANVA, name="1K", price=1234.5, stock=3, sort_order=0, active=True)
    with admin.c.session_transaction() as s:
        s["locale"] = "tr"
    page = admin.html(f"/admin/listings/{CANVA}/edit")
    assert 'value="1234.50"' in page  # <input type="number"> needs a dot
    assert variant_id
    assert "1.234,50" in admin.html("/admin/listings")


def test_formatting_never_changes_amounts(make_client):
    """The same cart costs exactly the same in Turkish and English; stored prices are untouched."""
    before = in_app(store.get_product, CANVA)["base_price"]
    tr = place(make_client(locale="tr", ip="10.130.0.4"), "a@x.test")
    en = place(make_client(locale="en", ip="10.130.0.5"), "b@x.test")
    assert in_app(store.get_order_by_id, tr["id"])["total_price"] == in_app(store.get_order_by_id, en["id"])["total_price"] == 9.99
    assert in_app(store.get_product, CANVA)["base_price"] == before == 9.99
