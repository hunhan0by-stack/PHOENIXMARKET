"""Data safety: restoring a backup, outbox retention and reset links kept out of the outbox."""
import re
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import app
import services.backup as backup
from services.database import init_schema
import services.store as store
from tests.helpers import in_app
from tests.test_emails import FakeSMTP, smtp  # noqa: F401  (fixture)


@pytest.fixture
def backup_folder(tmp_path):
    previous = app.config.get("BACKUP_DIR")
    app.config["BACKUP_DIR"] = str(tmp_path / "backups")
    yield tmp_path / "backups"
    app.config["BACKUP_DIR"] = previous


def table_counts(path) -> dict:
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


def test_backup_restores_into_a_working_shop(client, admin, backup_folder, tmp_path, monkeypatch):
    """The restore procedure of docs/DATA_BACKUP_RECOVERY.md, on an isolated copy."""
    client.post("/cart/add/1")
    client.post("/checkout", {"full_name": "Restore Test", "email": "restore@x.com"})
    admin.post("/admin/listings/1/codes", {"codes": "RESTORE-1\nRESTORE-2"})
    order = in_app(store.list_orders_admin)[0]
    admin.post(f"/admin/orders/{order['id']}/payment", {"payment_ref": "R-1"})

    live = table_counts(app.config["DATABASE_PATH"])
    copy = in_app(backup.create_backup)
    restored = tmp_path / "restored" / "phoenixmarket.db"
    restored.parent.mkdir()
    shutil.copy(copy, restored)

    conn = sqlite3.connect(restored)
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    assert table_counts(restored) == live

    # Start the app on the restored file: schema updates apply and the data reads back
    monkeypatch.setitem(app.config, "DATABASE_PATH", str(restored))
    in_app(init_schema)
    back = in_app(store.get_order_by_id, order["id"])
    assert back["user_email"] == "restore@x.com" and back["fulfillment_status"] == "completed"
    assert in_app(store.count_codes_for_order, order["id"]) == 1
    assert len(in_app(store.list_code_stock, 1)[None]) == 1
    assert table_counts(restored) == live


def test_reset_link_is_not_kept_in_the_outbox_when_emails_are_sent(make_client, smtp):  # noqa: F811
    user = make_client(ip="10.70.0.1")
    user.signup(email="reset@x.com", password="oldpassword")
    anon = make_client(ip="10.70.0.2")
    anon.post("/forgot-password", {"email": "reset@x.com"})
    [delivered] = smtp.sent
    link = re.search(r"http://localhost/reset-password/\S+", delivered.get_body(("plain",)).get_content()).group(0)
    [stored] = [e for e in in_app(store.list_emails) if e["kind"] == "password_reset"]
    token = link.rsplit("/", 1)[1]
    assert token not in stored["body_text"] and token not in (stored["body_html"] or "")
    assert "[removed for security]" in stored["body_text"]
    # The delivered link still works
    assert anon.get(link.replace("http://localhost", "")).status_code == 200


def test_outbox_retention_is_off_by_default_and_deletes_only_old_entries():
    now = datetime.now(timezone.utc)

    def add(created):
        db = store.get_db()
        db.execute(
            "INSERT INTO email_outbox (to_address, subject, body_text, kind, status, created_at) "
            "VALUES ('a@x.com', 's', 'b', 'k', 'logged', ?)",
            (created.isoformat(),),
        )
        db.commit()

    in_app(add, now - timedelta(days=200))
    in_app(add, now - timedelta(days=5))
    assert app.config["OUTBOX_RETENTION_DAYS"] == 0
    assert in_app(store.purge_old_emails, 0) == 0 and in_app(store.count_emails) == 2
    assert in_app(store.purge_old_emails, 90) == 1 and in_app(store.count_emails) == 1
