"""Order fields: what a listing asks for at checkout, and where the answers show up."""
import json

import services.store as store
from tests.helpers import in_app

INSTAGRAM = 2
FIELDS = [
    {"label": "Instagram username", "kind": "text", "required": True},
    {"label": "Post link", "kind": "url", "required": False},
]


def with_fields(product_id=INSTAGRAM, fields=FIELDS):
    in_app(store.update_product, product_id, {"order_fields": fields})


def save_listing(admin, product_id=INSTAGRAM, **over):
    product = in_app(store.get_product, product_id)
    data = {
        "name": product["name"], "description": product["description"],
        "base_price": str(product["base_price"]), "base_currency": "USD", "listing_language": "en",
        "category": product["category"], "stock": str(product["stock"]), "status": "active",
    }
    data.update(over)
    return admin.post(f"/admin/listings/{product_id}/edit", data)


def checkout(client, **answers):
    return client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com", **answers})


def test_admin_saves_order_fields(admin):
    save_listing(
        admin,
        field_label_0="  Instagram   username ", field_kind_0="text", field_required_0="1",
        field_label_1="Post link", field_kind_1="url",
        field_label_2="",
    )
    assert in_app(store.get_product, INSTAGRAM)["order_fields"] == FIELDS
    html = admin.html(f"/admin/listings/{INSTAGRAM}/edit")
    assert 'value="Instagram username"' in html and 'value="Post link"' in html


def test_admin_can_remove_order_fields(admin):
    with_fields()
    save_listing(admin)
    assert in_app(store.get_product, INSTAGRAM)["order_fields"] == []


def test_overlong_field_name_is_rejected_and_kept_for_correction(admin):
    r = save_listing(admin, field_label_0="x" * 61)
    assert r.status_code == 200 and "at most 60 characters" in r.get_data(as_text=True)
    assert in_app(store.get_product, INSTAGRAM)["order_fields"] == []


def test_checkout_asks_for_the_listings_fields(client):
    with_fields()
    client.post(f"/cart/add/{INSTAGRAM}")
    html = client.html("/checkout")
    assert "Instagram username" in html and f'name="field-{INSTAGRAM}-0"' in html
    assert 'type="url"' in html


def test_required_field_must_be_filled(client):
    with_fields()
    client.post(f"/cart/add/{INSTAGRAM}")
    r = checkout(client, notes="keep me")
    assert r.status_code == 200 and in_app(store.count_orders) == 0
    body = r.get_data(as_text=True)
    assert "Please fill in &#34;Instagram username&#34; for Instagram Followers" in body
    assert "keep me" in body  # the rest of the form is not lost


def test_link_field_must_be_a_link(client):
    with_fields()
    client.post(f"/cart/add/{INSTAGRAM}")
    r = checkout(client, **{f"field-{INSTAGRAM}-0": "@me", f"field-{INSTAGRAM}-1": "not a link"})
    assert r.status_code == 200 and in_app(store.count_orders) == 0
    assert 'value="@me"' in r.get_data(as_text=True)


def test_answers_are_stored_with_the_order_and_shown(client, admin):
    with_fields()
    client.post(f"/cart/add/{INSTAGRAM}")
    r = checkout(client, **{f"field-{INSTAGRAM}-0": " @phoenix ", f"field-{INSTAGRAM}-1": ""})
    assert "/checkout/complete/" in r.headers["Location"]
    order = in_app(store.list_orders_admin)[0]
    full = in_app(store.get_order_by_id, order["id"], include_items=True)
    # Empty optional answers are left out
    assert full["line_items"][0]["inputs"] == [{"label": "Instagram username", "value": "@phoenix"}]

    # A renamed field does not change what the order shows
    with_fields(fields=[{"label": "Profile", "kind": "text", "required": True}])
    assert "Instagram username: <span" in client.html(f"/track/{full['tracking_token']}")
    assert "@phoenix" in admin.html(f"/admin/orders/{full['id']}")
    csv_text = admin.get("/admin/orders.csv").get_data(as_text=True)
    assert "[Instagram username: @phoenix]" in csv_text

    emails = in_app(store.list_emails)
    confirmation = next(e for e in emails if e["kind"] == "order_confirmation")
    assert "Instagram username: @phoenix" in confirmation["body_text"]


def test_listing_without_fields_checks_out_as_before(client):
    client.post(f"/cart/add/{INSTAGRAM}")
    r = checkout(client)
    assert "/checkout/complete/" in r.headers["Location"]
    order = in_app(store.list_orders_admin)[0]
    assert in_app(store.get_order_by_id, order["id"], include_items=True)["line_items"][0]["inputs"] == []


def test_account_export_contains_the_answers(make_client):
    with_fields()
    c = make_client(ip="10.41.0.1")
    c.signup(email="veri@x.com", password="uzunsifre1")
    c.post(f"/cart/add/{INSTAGRAM}")
    c.post("/checkout", {"full_name": "Veri", "email": "veri@x.com", f"field-{INSTAGRAM}-0": "@veri"})
    data = json.loads(c.get("/account/export").data)
    assert data["orders"][0]["items"][0]["details"] == [{"label": "Instagram username", "value": "@veri"}]
