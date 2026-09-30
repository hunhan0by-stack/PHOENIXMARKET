"""Admin order search/filter/pagination, CSV export and the order timeline."""
import uuid

import services.store as store
from tests.helpers import in_app


def make_order(email="buyer@x.com", name="Buyer", qty=1, product_id=1):
    return in_app(
        store.create_order,
        tracking_token=str(uuid.uuid4()),
        user_email=email,
        full_name=name,
        notes="",
        total_price=9.99 * qty,
        total_currency="USD",
        line_items=[{"product_id": product_id, "product_name": "Canva Pro Subscription",
                     "quantity": qty, "unit_price_usd": 9.99}],
    )


def set_status(order_id, status):
    in_app(store.update_order_fulfillment, order_id, fulfillment_status=status,
           tracking_number=None, carrier_note=None)


def test_search_by_email_name_and_id(admin):
    first = make_order(email="alice@x.com", name="Alice")
    make_order(email="bob@x.com", name="Bob")
    html = admin.html("/admin/orders?q=alice")
    assert "alice@x.com" in html and "bob@x.com" not in html
    html = admin.html(f"/admin/orders?q=%23{first}")
    assert f"#{first}" in html and "bob@x.com" not in html
    html = admin.html("/admin/orders?q=%25")  # a literal "%" must not match everything
    assert "alice@x.com" not in html


def test_status_filter_and_counts(admin):
    a, b = make_order(email="p@x.com"), make_order(email="c@x.com")
    set_status(b, "completed")
    assert in_app(store.count_orders_by_status) == {"pending": 1, "completed": 1}
    html = admin.html("/admin/orders?status=completed")
    assert "c@x.com" in html and "p@x.com" not in html
    html = admin.html("/admin/orders?status=bogus")  # unknown status -> no filter
    assert "c@x.com" in html and "p@x.com" in html


def test_pagination(admin):
    for i in range(30):
        make_order(email=f"u{i:02d}@x.com")
    page1 = admin.html("/admin/orders")
    page2 = admin.html("/admin/orders?page=2")
    assert "u29@x.com" in page1 and "u04@x.com" not in page1
    assert "u04@x.com" in page2 and "u29@x.com" not in page2
    assert 'aria-label="Pagination"' in page1
    assert "u04@x.com" in admin.html("/admin/orders?page=99")  # clamped to the last page


def test_csv_export_respects_filters_and_neutralises_formulas(admin):
    make_order(email="evil@x.com", name="=HYPERLINK(\"http://x\")", qty=2)
    other = make_order(email="done@x.com")
    set_status(other, "completed")
    r = admin.get("/admin/orders.csv?status=pending")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    text = r.get_data(as_text=True)
    assert text.startswith("﻿id,created_at,status")
    assert "'=HYPERLINK" in text and "Canva Pro Subscription x2" in text
    assert "done@x.com" not in text


def test_csv_export_requires_admin(client):
    r = client.get("/admin/orders.csv")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin/login")


def test_timeline_shows_customer_events_but_hides_internal_notes(admin, client):
    order_id = make_order()
    token = in_app(store.get_order_by_id, order_id)["tracking_token"]
    admin.post(f"/admin/orders/{order_id}", {
        "fulfillment_status": "processing",
        "tracking_number": "TRK-123",
        "carrier_note": "We started working on it",
    })
    admin.post(f"/admin/orders/{order_id}/notes", {"note": "Payment checked on itemsatis"})
    events = in_app(store.list_order_events, order_id)
    assert [e["event_type"] for e in events] == [
        "status_changed", "tracking_updated", "customer_note", "internal_note",
    ]

    public = client.html(f"/track/{token}")
    assert "Status changed to Processing" in public
    assert "Tracking number added: TRK-123" in public
    assert "We started working on it" in public
    assert "Payment checked on itemsatis" not in public

    admin_html = admin.html(f"/admin/orders/{order_id}")
    assert "Payment checked on itemsatis" in admin_html and "Internal note (admins only)" in admin_html


def test_saving_without_changes_adds_no_events(admin):
    order_id = make_order()
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": "pending"})
    assert in_app(store.list_order_events, order_id) == []


def test_empty_internal_note_is_rejected(admin):
    order_id = make_order()
    admin.post(f"/admin/orders/{order_id}/notes", {"note": "   "})
    assert any("cannot be empty" in m for m in admin.flashes())
    assert in_app(store.list_order_events, order_id) == []
