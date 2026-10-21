"""Sales report on the admin dashboard."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import services.store as store
from tests.helpers import in_app


def order_on(days_ago, qty=1, product_id=2, name="Instagram Followers", price=14.99, status="pending"):
    order_id = in_app(
        store.create_order,
        tracking_token=str(uuid.uuid4()), user_email="s@x.com", full_name="S", notes="",
        total_price=price * qty, total_currency="USD",
        line_items=[{"product_id": product_id, "product_name": name, "quantity": qty, "unit_price_usd": price}],
    )
    when = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()

    def backdate():
        db = store.get_db()
        db.execute("UPDATE orders SET created_at = ?, fulfillment_status = ? WHERE id = ?", (when, status, order_id))
        db.commit()

    in_app(backdate)
    return order_id


def test_sales_report_window_and_totals(make_client):
    order_on(0, qty=2)                                          # 29.98 today
    order_on(3, product_id=1, name="Canva Pro Subscription", price=9.99, status="completed")
    order_on(0, status="cancelled")                             # excluded
    order_on(40)                                                # outside the window
    for ip in ("10.60.0.1", "10.60.0.2"):
        make_client(ip=ip).get("/")

    report = in_app(store.sales_report, 30)
    assert len(report["series"]) == 30
    assert report["orders"] == 2
    assert report["value"] == pytest.approx(39.97)
    assert report["completed_revenue"] == pytest.approx(9.99)
    assert report["average_order"] == pytest.approx(19.99, abs=0.01)
    assert report["series"][-1]["orders"] == 1 and report["series"][-1]["value"] == pytest.approx(29.98)
    assert report["series"][-4]["value"] == pytest.approx(9.99)
    assert [p["name"] for p in report["top_products"]] == ["Instagram Followers", "Canva Pro Subscription"]
    assert report["top_products"][0]["quantity"] == 2
    assert report["visitors"] == 2 and report["conversion"] == 100.0


def test_empty_report():
    report = in_app(store.sales_report, 30)
    # No visits recorded: no conversion rate rather than a misleading 0%
    assert report["orders"] == 0 and report["average_order"] == 0 and report["conversion"] is None


def test_dashboard_shows_sales_chart(admin):
    order_on(1)
    html = admin.html("/admin/dashboard")
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    assert "Sales · last 30 days" in html
    assert f"<title>{day}: $14.99 · Orders: 1</title>" in html
    assert "Instagram Followers" in html.split("Top services by order value", 1)[1]
