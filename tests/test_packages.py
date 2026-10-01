"""
Product packages (variants): admin management, package choice, prices and
stock from the server, checkout, cancellation and how orders show packages.
"""
import json
import re

import pytest

import services.store as store
from tests.helpers import in_app, set_stock, stock_of

INSTAGRAM = 2  # seeded listing, 14.99 USD, own stock 200


def outbox(kind):
    return [e for e in in_app(store.list_emails, 100) if e["kind"] == kind]


def add_package(admin, product_id=INSTAGRAM, name="1K", price="4.99", stock="10", **extra):
    data = {"name": name, "price": price, "stock": stock, "sort_order": "0", "active": "1", **extra}
    return admin.post(f"/admin/listings/{product_id}/packages", data)


def packages(product_id=INSTAGRAM):
    return in_app(store.list_variants, product_id)


def package_id(name, product_id=INSTAGRAM):
    return next(v["id"] for v in packages(product_id) if v["name"] == name)


def variant(variant_id):
    return in_app(store.get_variant, variant_id)


@pytest.fixture
def instagram_packages(admin):
    """Instagram Followers sold as 1K (4.99, 10 left) and 5K (19.99, 3 left)."""
    add_package(admin, name="1K", price="4.99", stock="10")
    add_package(admin, name="5K", price="19.99", stock="3", sort_order="1")
    return package_id("1K"), package_id("5K")


def checkout(client, email="buyer@x.com"):
    return client.post("/checkout", {"full_name": "Buyer", "email": email})


def last_order():
    orders = in_app(store.list_orders_admin)
    return in_app(store.get_order_by_id, orders[0]["id"]) if orders else None


# --- admin management -------------------------------------------------------------
def test_admin_adds_packages_and_listing_switches_to_package_pricing(admin, instagram_packages):
    product = in_app(store.get_product, INSTAGRAM)
    assert product["has_variants"] and product["price_from"]
    assert product["base_price"] == 4.99 and product["max_price"] == 19.99
    assert product["stock"] == 13  # summed package stock
    assert product["own_price"] == 14.99 and product["own_stock"] == 200


@pytest.mark.parametrize(
    "fields, error",
    [
        ({"name": ""}, "needs a name"),
        ({"name": "x" * 81}, "needs a name"),
        ({"price": "0"}, "price above 0"),
        ({"price": "-5"}, "price above 0"),
        ({"price": "abc"}, "price above 0"),
        ({"price": "nan"}, "price above 0"),
        ({"stock": "-1"}, "price above 0"),
        ({"name": "1k"}, "already has a package"),  # duplicate, case-insensitive
    ],
)
def test_package_form_validation(admin, instagram_packages, fields, error):
    admin.clear_flashes()
    add_package(admin, **{"name": "10K", "price": "29.99", "stock": "5", **fields})
    assert len(packages()) == 2
    assert any(error in m for m in admin.flashes())


def test_package_of_another_listing_cannot_be_edited_or_deleted(admin, instagram_packages):
    one_k, _ = instagram_packages
    admin.post(f"/admin/listings/3/packages/{one_k}", {"name": "Hacked", "price": "1", "stock": "1"})
    admin.post(f"/admin/listings/3/packages/{one_k}/delete")
    assert variant(one_k)["name"] == "1K"


def test_package_routes_require_admin(client, admin, instagram_packages):
    one_k, _ = instagram_packages
    r = client.post(f"/admin/listings/{INSTAGRAM}/packages/{one_k}/delete")
    assert r.headers["Location"].endswith("/admin/login")
    assert variant(one_k) is not None


def test_edit_and_delete_package(admin, instagram_packages):
    one_k, five_k = instagram_packages
    admin.post(
        f"/admin/listings/{INSTAGRAM}/packages/{one_k}",
        {"name": "1K Followers", "price": "5.49", "stock": "7", "sort_order": "2"},  # no "active": unchecked
    )
    v = variant(one_k)
    assert (v["name"], v["price"], v["stock"], v["active"]) == ("1K Followers", 5.49, 7, False)
    admin.post(f"/admin/listings/{INSTAGRAM}/packages/{five_k}/delete")
    assert variant(five_k) is None


def test_listing_form_edits_own_price_and_stock_not_package_totals(admin, instagram_packages):
    html = admin.html(f"/admin/listings/{INSTAGRAM}/edit")
    assert 'name="stock" value="200"' in html and 'value="14.99"' in html
    assert "sold per package" in html and "1K" in html and "5K" in html
    # Saving the listing without touching stock keeps its own stock
    admin.post(f"/admin/listings/{INSTAGRAM}/edit", {
        "name": "Instagram Followers", "description": "Grow your account", "base_price": "14.99",
        "base_currency": "USD", "listing_language": "en", "category": "Social Media", "status": "active",
    })
    assert in_app(store.get_product, INSTAGRAM)["own_stock"] == 200


def test_all_packages_inactive_means_nothing_to_sell(admin, client, instagram_packages):
    for v in packages():
        admin.post(f"/admin/listings/{INSTAGRAM}/packages/{v['id']}",
                   {"name": v["name"], "price": str(v["price"]), "stock": str(v["stock"])})
    product = in_app(store.get_product, INSTAGRAM)
    assert product["has_variants"] and product["stock"] == 0
    client.post(f"/cart/add/{INSTAGRAM}")
    assert not client.session().get("cart")


# --- storefront -------------------------------------------------------------------
def test_catalog_shows_from_price_and_choose_package(client, make_client, instagram_packages):
    html = client.html("/products")
    assert "From 4.99 $" in html and "Choose a package" in html
    tr = make_client(locale="tr").html("/products")
    assert "Başlangıç" in tr and "Paket seçin" in tr


def test_product_page_lists_active_packages_with_stock(client, admin, instagram_packages):
    add_package(admin, name="Hidden", price="1", stock="5", active="")
    admin.post(f"/admin/listings/{INSTAGRAM}/packages/{package_id('Hidden')}",
               {"name": "Hidden", "price": "1", "stock": "5"})
    html = client.html(f"/product/{INSTAGRAM}")
    assert 'name="variant_id"' in html
    assert "1K" in html and "19.99 $" in html and "Only 3 left" in html
    assert "Hidden" not in html


def test_product_jsonld_uses_aggregate_offer(client, instagram_packages):
    html = client.html(f"/product/{INSTAGRAM}")
    data = json.loads(re.search(r'application/ld\+json">(.*?)</script>', html, re.S).group(1))
    assert data["offers"]["@type"] == "AggregateOffer"
    assert data["offers"]["lowPrice"] == "4.99" and data["offers"]["highPrice"] == "19.99"


# --- cart -------------------------------------------------------------------------
def test_listing_with_packages_requires_a_package(client, instagram_packages):
    r = client.post(f"/cart/add/{INSTAGRAM}")
    assert r.headers["Location"].endswith("/product/instagram-followers")
    assert not client.session().get("cart")
    assert any("choose a package" in m for m in client.flashes())


def test_add_package_to_cart(client, instagram_packages):
    one_k, _ = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k), "qty": "2"})
    assert client.session()["cart"] == {f"{INSTAGRAM}:{one_k}": 2}
    html = client.html("/cart")
    assert "Package: <span" in html and "1K" in html
    assert "9.98 $" in html  # 2 x 4.99, the package price


@pytest.mark.parametrize("bad", ["999", "abc", "-1", ""])
def test_unknown_package_is_rejected(client, instagram_packages, bad):
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": bad})
    assert not client.session().get("cart")


def test_package_of_another_listing_is_rejected(client, admin, instagram_packages):
    add_package(admin, product_id=3, name="10K views", price="2", stock="5")
    foreign = package_id("10K views", product_id=3)
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(foreign)})
    assert not client.session().get("cart")


def test_package_id_for_listing_without_packages_is_rejected(client, instagram_packages):
    one_k, _ = instagram_packages
    client.post("/cart/add/1", {"variant_id": str(one_k)})
    assert not client.session().get("cart")


def test_inactive_package_or_listing_cannot_be_added(client, admin, instagram_packages):
    one_k, five_k = instagram_packages
    in_app(store.update_variant, one_k, "1K", 4.99, 10, 0, False)
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    in_app(store.update_product, INSTAGRAM, {"status": "inactive"})
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(five_k)})
    assert not client.session().get("cart")


def test_cart_quantity_is_limited_to_package_stock(client, instagram_packages):
    _, five_k = instagram_packages
    key = f"{INSTAGRAM}:{five_k}"
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(five_k), "qty": "50"})
    assert client.session()["cart"][key] == 3
    client.post(f"/cart/update/{key}", {"qty": "9"})
    assert client.session()["cart"][key] == 3
    client.post(f"/cart/update/{key}", {"qty": "1"})
    assert client.session()["cart"][key] == 1
    client.post(f"/cart/update/{key}", {"qty": "0"})
    assert key not in client.session()["cart"]


def test_stale_cart_lines_are_pruned(client, admin, instagram_packages):
    one_k, five_k = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(five_k)})
    client.post("/cart/add/1")
    # Package deleted, and a plain line forged for a listing now sold per package
    admin.post(f"/admin/listings/{INSTAGRAM}/packages/{five_k}/delete")
    with client.c.session_transaction() as s:
        s["cart"][str(INSTAGRAM)] = 1
    client.get("/cart")
    assert client.session()["cart"] == {f"{INSTAGRAM}:{one_k}": 1, "1": 1}


# --- checkout and orders -------------------------------------------------------------
def test_checkout_stores_package_and_takes_package_stock(client, instagram_packages):
    one_k, _ = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k), "qty": "2"})
    client.post("/cart/add/1")
    r = checkout(client)
    assert "/checkout/complete/" in r.headers["Location"]
    order = last_order()
    lines = {li["display_name"]: li for li in order["line_items"]}
    pkg_line = lines["Instagram Followers – 1K"]
    assert pkg_line["variant_id"] == one_k and pkg_line["variant_name"] == "1K"
    assert pkg_line["price"] == 4.99 and pkg_line["quantity"] == 2
    assert order["total_price"] == pytest.approx(2 * 4.99 + 9.99)
    assert variant(one_k)["stock"] == 8
    assert in_app(store.get_product, INSTAGRAM)["own_stock"] == 200  # untouched


def test_checkout_uses_current_database_price(client, instagram_packages):
    one_k, _ = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    in_app(store.update_variant, one_k, "1K", 6.50, 10, 0, True)
    checkout(client)
    assert last_order()["line_items"][0]["price"] == 6.50


def test_package_deactivated_after_adding_blocks_checkout(client, instagram_packages):
    one_k, _ = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    in_app(store.update_variant, one_k, "1K", 4.99, 10, 0, False)
    r = checkout(client)
    assert r.headers["Location"].endswith("/cart")
    assert in_app(store.count_orders) == 0
    assert variant(one_k)["stock"] == 10


def test_last_package_unit_goes_to_one_buyer(make_client, instagram_packages):
    _, five_k = instagram_packages
    in_app(store.update_variant, five_k, "5K", 19.99, 1, 1, True)
    first, second = make_client(), make_client(ip="10.0.0.2")
    for c in (first, second):
        c.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(five_k)})
    assert "/checkout/complete/" in checkout(first).headers["Location"]
    assert checkout(second, "b@x.com").headers["Location"].endswith("/cart")
    assert variant(five_k)["stock"] == 0
    assert in_app(store.count_orders) == 1


def test_order_line_without_package_cannot_buy_listing_sold_per_package(instagram_packages):
    with pytest.raises(store.OutOfStockError):
        in_app(
            store.create_order,
            tracking_token="forged", user_email="f@x.com", full_name="F", notes="",
            total_price=1, total_currency="USD",
            line_items=[{"product_id": INSTAGRAM, "product_name": "Instagram Followers",
                         "quantity": 1, "unit_price_usd": 0.01}],
        )
    assert in_app(store.get_product, INSTAGRAM)["own_stock"] == 200


def test_cancel_returns_package_stock_once_and_reopen_reserves(client, admin, instagram_packages):
    one_k, _ = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k), "qty": "4"})
    checkout(client)
    order = last_order()
    assert variant(one_k)["stock"] == 6
    client.post(f"/track/{order['tracking_token']}/cancel")
    client.post(f"/track/{order['tracking_token']}/cancel")  # double submit
    assert variant(one_k)["stock"] == 10
    admin.post(f"/admin/orders/{order['id']}", {"fulfillment_status": "processing"})
    assert variant(one_k)["stock"] == 6


def test_deleted_package_keeps_order_history(client, admin, instagram_packages):
    one_k, _ = instagram_packages
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    checkout(client)
    order = last_order()
    admin.post(f"/admin/listings/{INSTAGRAM}/packages/{one_k}/delete")
    admin.post(f"/admin/orders/{order['id']}", {"fulfillment_status": "cancelled"})  # nothing to return to
    assert in_app(store.get_order_by_id, order["id"])["line_items"][0]["display_name"] == "Instagram Followers – 1K"
    assert "Instagram Followers – 1K" in admin.html(f"/admin/orders/{order['id']}")


def test_package_name_shown_to_customer_admin_email_and_csv(client, admin, instagram_packages):
    one_k, _ = instagram_packages
    client.signup()
    client.post(f"/cart/add/{INSTAGRAM}", {"variant_id": str(one_k)})
    checkout(client, email="user@x.com")
    order = last_order()
    name = "Instagram Followers – 1K"
    assert name in client.html(f"/track/{order['tracking_token']}")
    assert name in client.html("/account/orders")
    assert name in admin.html(f"/admin/orders/{order['id']}")
    assert name in admin.get("/admin/orders.csv").get_data(as_text=True)
    assert name in outbox("order_confirmation")[0]["body_text"]
    assert name in admin.html("/admin/dashboard")  # best sellers


# --- back-in-stock ------------------------------------------------------------------
def test_restocking_a_package_sends_back_in_stock_emails(client, admin, instagram_packages):
    one_k, five_k = instagram_packages
    for v_id, name in ((one_k, "1K"), (five_k, "5K")):
        in_app(store.update_variant, v_id, name, 4.99, 0, 0, True)
    assert stock_of(INSTAGRAM) == 0
    client.post(f"/product/{INSTAGRAM}/notify", {"email": "wait@x.com"})
    admin.post(f"/admin/listings/{INSTAGRAM}/packages/{five_k}",
               {"name": "5K", "price": "19.99", "stock": "4", "active": "1"})
    assert [e["to_address"] for e in outbox("back_in_stock")] == ["wait@x.com"]


def test_listing_without_packages_still_works_as_before(client):
    set_stock(1, 3)
    client.post("/cart/add/1", {"qty": "2"})
    assert client.session()["cart"] == {"1": 2}
    checkout(client)
    assert stock_of(1) == 1
