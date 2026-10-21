"""Visit analytics without personal data: no IPs, daily ids, retention, bots."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import app
import services.store as store
from services.web import visitor_id
from tests.helpers import in_app

CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"


def visits():
    def rows():
        return [dict(r) for r in store.get_db().execute("SELECT * FROM visits ORDER BY id")]
    return in_app(rows)


def insert_visit(ts, ip, ua):
    def run():
        db = store.get_db()
        db.execute("INSERT INTO visits (ts, path, ip, user_agent) VALUES (?, '/', ?, ?)", (ts, ip, ua))
        db.commit()
    in_app(run)


def test_page_view_stores_no_ip_and_only_the_browser(make_client):
    make_client(ip="203.0.113.7").get("/", headers={"User-Agent": CHROME})
    [row] = visits()
    assert "203.0.113.7" not in str(row)
    assert re.fullmatch(r"[0-9a-f]{16}", row["ip"])
    assert row["user_agent"] == "Chrome"


def test_visitor_id_is_stable_within_a_day_only():
    with app.app_context():
        same_day = visitor_id("203.0.113.7", "2026-10-21"), visitor_id("203.0.113.7", "2026-10-21")
        next_day = visitor_id("203.0.113.7", "2026-10-22")
        other_ip = visitor_id("203.0.113.8", "2026-10-21")
    assert same_day[0] == same_day[1]
    assert next_day != same_day[0] and other_ip != same_day[0]


@pytest.mark.parametrize("ua", [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "curl/8.4.0",
    "python-requests/2.32",
    "UptimeRobot/2.0",
    "",
])
def test_bots_and_scripts_are_not_counted(make_client, ua):
    make_client().get("/", headers={"User-Agent": ua})
    assert visits() == []


def test_admin_pages_and_the_admin_are_not_counted(admin, make_client):
    browser = make_client()
    for path in ("/admin/login", "/set-locale/tr", "/set-currency/EUR"):
        browser.get(path, headers={"User-Agent": CHROME})
    admin.get("/products", headers={"User-Agent": CHROME})  # the owner browsing the shop
    browser.post("/cart/add/2", headers={"User-Agent": CHROME})  # not a page view
    assert visits() == []


def test_legacy_raw_ips_are_anonymised_once():
    insert_visit("2026-09-30T10:00:00+00:00", "198.51.100.4", CHROME)
    changed = in_app(store.anonymize_legacy_visits, visitor_id)
    assert changed == 1
    [row] = visits()
    with app.app_context():
        assert row["ip"] == visitor_id("198.51.100.4", "2026-09-30")
    assert row["user_agent"] == "Chrome"
    assert in_app(store.anonymize_legacy_visits, visitor_id) == 0


def test_old_page_views_are_deleted_after_retention():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=store.VISIT_RETENTION_DAYS + 1)).isoformat()
    recent = (now - timedelta(days=store.VISIT_RETENTION_DAYS - 1)).isoformat()
    insert_visit(old, "a" * 16, "Chrome")
    insert_visit(recent, "b" * 16, "Chrome")
    assert in_app(store.purge_old_visits) == 1
    assert [v["ip"] for v in visits()] == ["b" * 16]


def test_row_cap_keeps_the_newest(monkeypatch):
    monkeypatch.setattr(store, "ANALYTICS_MAX_ENTRIES", 2)
    ts = datetime.now(timezone.utc).isoformat()
    for ch in "abc":
        insert_visit(ts, ch * 16, "Chrome")
    in_app(store.purge_old_visits)
    assert [v["ip"] for v in visits()] == ["b" * 16, "c" * 16]


def test_dashboard_shows_visitors_without_ips(admin, make_client):
    make_client(ip="203.0.113.9").get("/products", headers={"User-Agent": CHROME})
    html = admin.html("/admin/dashboard")
    assert "203.0.113.9" not in html
    assert "No IP addresses are stored" in html
    analytics = in_app(store.get_analytics_for_admin)
    assert analytics["total_visitors"] >= 1
    assert ("/products", 1) in analytics["top_pages"]
    assert any(v["user_agent_summary"] == "Chrome" for v in analytics["recent_visits"])


def test_conversion_is_not_shown_without_enough_visits(admin, client):
    """More orders than visitor-days (e.g. orders older than kept visits): no rate."""
    for email in ("a@x.com", "b@x.com"):
        client.post("/cart/add/2")
        client.post("/checkout", {"full_name": "A", "email": email})

    def drop_visits():
        db = store.get_db()
        db.execute("DELETE FROM visits")
        db.commit()

    in_app(drop_visits)
    assert in_app(store.sales_report, 30)["conversion"] is None
    assert "Not enough visit data for a rate" in admin.html("/admin/dashboard")
