"""Security audit log: what is recorded, and that secrets never are."""
import re
import time
from pathlib import Path

import services.store as store
from services.translations import TRANSLATIONS
import services.totp as totp
from tests.helpers import ADMIN_LOGIN, in_app

ROOT = Path(__file__).resolve().parents[1]


def events(name=None):
    found = in_app(store.list_security_events)
    return [e for e in found if name is None or e["event"] == name]


def all_logged_text():
    return " ".join(f"{e['event']} {e['actor']} {e['detail']}" for e in events())


def test_admin_sign_ins_are_recorded_without_credentials(make_client):
    c = make_client(ip="10.80.0.1")
    c.post("/admin/login", {"username": "admin", "password": "Wrong-Secret-123"})
    [failed] = events("admin_login_failed")
    assert failed["ip"] == "10.80.0.1" and failed["actor"] == "admin"
    c.login_admin()
    assert events("admin_login")
    assert "Wrong-Secret-123" not in all_logged_text() and ADMIN_LOGIN["password"] not in all_logged_text()


def test_payment_delivery_and_code_stock_are_recorded_without_codes(client, admin):
    admin.post("/admin/listings/1/codes", {"codes": "SECRET-CODE-1\nSECRET-CODE-2"})
    client.post("/cart/add/1")
    client.post("/checkout", {"full_name": "Buyer", "email": "buyer@x.com"})
    order = in_app(store.list_orders_admin)[0]
    admin.post(f"/admin/orders/{order['id']}/payment", {"payment_ref": "IS-9"})
    admin.post(f"/admin/orders/{order['id']}/delivery", {"delivery": "pass: Hunter2-Secret"})
    assert events("codes_added")[0]["detail"] == "listing #1: 2"
    assert events("payment_confirmed")[0]["detail"] == f"order #{order['id']}"
    assert events("order_auto_delivered") and events("order_delivered")
    text = all_logged_text()
    assert "SECRET-CODE" not in text and "Hunter2" not in text


def test_two_step_changes_are_recorded_without_the_secret(admin):
    secret = in_app(totp.pending_secret)
    admin.post("/admin/security/enable", {"code": totp.code_at(secret, int(time.time() // 30))})
    assert events("admin_2fa_enabled")
    assert secret not in all_logged_text()


def test_customer_data_requests_are_recorded_without_ip(make_client):
    c = make_client(ip="10.80.0.5")
    c.signup(email="veri@x.com", password="uzunsifre1")
    c.get("/account/export")
    c.post("/account/delete", {"password": "uzunsifre1", "confirm_delete": "1"})
    exported, deleted = events("account_exported")[0], events("account_deleted")[0]
    assert exported["actor"].startswith("customer #") and exported["ip"] is None and deleted["ip"] is None
    assert "veri@x.com" not in all_logged_text()


def test_backup_downloads_are_recorded(admin, tmp_path, monkeypatch):
    from app import app

    monkeypatch.setitem(app.config, "BACKUP_DIR", str(tmp_path))
    admin.post("/admin/backups")
    assert events("backup_downloaded")[0]["detail"].startswith("phoenixmarket-")


def test_security_log_is_admin_only(client, admin):
    admin.post("/admin/settings", {"itemsatis_redirect_url": "https://itemsatis.test/new"})
    assert "Settings changed" in admin.html("/admin/security")
    assert client.get("/admin/security").headers["Location"].endswith("/admin/login")


def test_every_recorded_event_has_a_label():
    names = set()
    for path in [*ROOT.glob("views/**/*.py"), *ROOT.glob("services/*.py"), ROOT / "app.py"]:
        names |= set(re.findall(r'(?:admin|customer|system)_event\(\s*"([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    assert names
    for locale in ("en", "tr"):
        missing = [n for n in names if f"sec_event_{n}" not in TRANSLATIONS[locale]]
        assert not missing, (locale, missing)
