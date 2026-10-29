"""Code stock: codes uploaded per listing/package, handed out when the payment is confirmed."""
from datetime import datetime, timedelta, timezone

import services.store as store
from tests.helpers import in_app, set_stock, stock_of

CANVA, INSTAGRAM = 1, 2


def upload(admin, codes, product_id=CANVA, **form):
    return admin.post(f"/admin/listings/{product_id}/codes", {"codes": "\n".join(codes), **form})


def place_order(client, *lines):
    """lines: (product_id, qty) or (product_id, qty, variant_id); returns the order."""
    for product_id, qty, *variant in lines:
        data = {"qty": str(qty)}
        if variant:
            data["variant_id"] = str(variant[0])
        client.post(f"/cart/add/{product_id}", data)
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    return in_app(store.list_orders_admin)[0]


def confirm_payment(admin, order_id, notify="1"):
    return admin.post(f"/admin/orders/{order_id}/payment", {"payment_ref": "IS-1", "notify_customer": notify})


def available(product_id=CANVA, variant_id=None):
    return len(in_app(store.list_code_stock, product_id).get(variant_id, []))


def test_uploading_codes_raises_stock_and_skips_duplicates(admin):
    set_stock(CANVA, 0)
    upload(admin, ["AAAA-1111", "BBBB-2222", "AAAA-1111", "  "])
    assert stock_of(CANVA) == 2 and available() == 2
    upload(admin, ["BBBB-2222", "CCCC-3333"])
    assert stock_of(CANVA) == 3 and available() == 3
    assert any("1 codes added (1 duplicates skipped)" in m for m in admin.flashes())
    html = admin.html(f"/admin/listings/{CANVA}/edit")
    assert "3 left" in html and "AAAA••••" in html and "AAAA-1111" not in html


def test_invalid_uploads_are_refused(admin):
    before = stock_of(CANVA)
    upload(admin, [])
    upload(admin, ["x" * 501])
    assert available() == 0 and stock_of(CANVA) == before


def test_paid_order_is_delivered_from_code_stock(client, admin):
    upload(admin, ["CODE-1", "CODE-2", "CODE-3"])
    order = place_order(client, (CANVA, 2))
    confirm_payment(admin, order["id"])
    full = in_app(store.get_order_by_id, order["id"])
    assert full["fulfillment_status"] == "completed"
    assert available() == 1

    html = client.html(f"/track/{full['tracking_token']}")
    assert "CODE-1" in html and "CODE-2" in html and "CODE-3" not in html
    emails = in_app(store.list_emails)
    assert any(e["kind"] == "delivery_ready" for e in emails)
    assert all("CODE-" not in e["body_text"] for e in emails)
    assert "automatic (code stock)" in admin.html(f"/admin/orders/{order['id']}")


def test_too_few_codes_hand_out_nothing(client, admin):
    upload(admin, ["ONLY-ONE"])
    set_stock(CANVA, 5)
    order = place_order(client, (CANVA, 2))
    confirm_payment(admin, order["id"])
    assert any("Not enough codes left for Canva Pro Subscription" in m for m in admin.flashes())
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "processing"
    assert available() == 1 and in_app(store.list_deliveries, order["id"]) == []
    notes = [e for e in in_app(store.list_order_events, order["id"]) if e["event_type"] == "internal_note"]
    assert notes and "not enough codes" in notes[0]["note"]


def test_listing_without_codes_is_delivered_by_hand_as_before(client, admin):
    order = place_order(client, (CANVA, 1))
    confirm_payment(admin, order["id"])
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "processing"
    assert in_app(store.list_deliveries, order["id"]) == []


def test_mixed_order_is_delivered_partly(client, admin):
    upload(admin, ["CODE-1"])
    order = place_order(client, (CANVA, 1), (INSTAGRAM, 1))
    confirm_payment(admin, order["id"])
    assert in_app(store.get_order_by_id, order["id"])["fulfillment_status"] == "processing"
    [d] = in_app(store.list_deliveries, order["id"])
    assert d["body"] == "Canva Pro Subscription:\nCODE-1"


def test_package_code_stock(client, admin):
    variant_id = in_app(store.create_variant, INSTAGRAM, name="1K", price=3.0, stock=0, sort_order=0, active=True)
    upload(admin, ["PKG-1"], product_id=INSTAGRAM)  # the listing itself is sold per package
    assert available(INSTAGRAM) == 0
    upload(admin, ["PKG-1"], product_id=INSTAGRAM, variant_id=str(variant_id))
    assert available(INSTAGRAM, variant_id) == 1 and in_app(store.get_variant, variant_id)["stock"] == 1
    order = place_order(client, (INSTAGRAM, 1, variant_id))
    confirm_payment(admin, order["id"])
    [d] = in_app(store.list_deliveries, order["id"])
    assert d["body"] == "Instagram Followers – 1K:\nPKG-1"


def test_codes_are_handed_out_once_per_order(client, admin):
    upload(admin, ["CODE-1", "CODE-2"])
    order = place_order(client, (CANVA, 1))
    confirm_payment(admin, order["id"])
    assert in_app(store.deliver_from_code_stock, order["id"], 14) == {"status": "already"}
    confirm_payment(admin, order["id"])  # double click: payment already recorded
    assert available() == 1 and len(in_app(store.list_deliveries, order["id"])) == 1


def test_unused_codes_can_be_deleted_handed_out_ones_cannot(client, admin):
    upload(admin, ["CODE-1", "CODE-2"])
    order = place_order(client, (CANVA, 1))
    confirm_payment(admin, order["id"])
    stock_before = stock_of(CANVA)
    [unused] = in_app(store.list_code_stock, CANVA)[None]
    admin.post(f"/admin/listings/{CANVA}/codes/{unused['id']}/delete")
    assert available() == 0 and stock_of(CANVA) == stock_before - 1
    assert not in_app(store.delete_code, CANVA, 1)  # CODE-1 belongs to the order


def test_undoing_the_payment_keeps_the_codes(client, admin):
    upload(admin, ["CODE-1"])
    order = place_order(client, (CANVA, 1))
    confirm_payment(admin, order["id"])
    admin.post(f"/admin/orders/{order['id']}/payment/undo")
    assert any("stay with it" in m for m in admin.flashes())
    assert in_app(store.count_codes_for_order, order["id"]) == 1


def test_handed_out_codes_are_cleared_with_the_delivery(client, admin):
    upload(admin, ["CODE-1"])
    order = place_order(client, (CANVA, 1))
    confirm_payment(admin, order["id"])
    in_app(store.purge_expired_deliveries, datetime.now(timezone.utc) + timedelta(days=60))

    def stored_codes():
        return [r["code"] for r in store.get_db().execute("SELECT code FROM delivery_codes")]

    assert in_app(stored_codes) == [None]
