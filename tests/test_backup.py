"""Database backups: verified copies, retention, daily trigger, admin download."""
import sqlite3
from datetime import timedelta

import pytest

import app as app_module
from app import app
import services.backup as backup
import services.store as store
from tests.helpers import in_app

SQLITE_MAGIC = b"SQLite format 3\x00"


@pytest.fixture(autouse=True)
def backup_folder(tmp_path):
    """Each test writes its copies to its own folder."""
    previous = app.config.get("BACKUP_DIR")
    app.config["BACKUP_DIR"] = str(tmp_path / "backups")
    yield tmp_path / "backups"
    app.config["BACKUP_DIR"] = previous


def product_count(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    finally:
        conn.close()


def test_backup_is_a_complete_self_contained_copy(backup_folder):
    in_app(store.set_config, "probe.key", "written-before-backup")
    path = in_app(backup.create_backup)

    assert path.parent == backup_folder
    assert path.read_bytes()[:16] == SQLITE_MAGIC
    assert not path.with_name(path.name + ".part").exists()
    conn = sqlite3.connect(path)
    try:
        # Single file (no WAL side files needed) and consistent
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        value = conn.execute("SELECT value FROM app_config WHERE key = 'probe.key'").fetchone()[0]
    finally:
        conn.close()
    assert value == "written-before-backup"
    assert product_count(path) == len(in_app(store.list_all_products))


def test_restoring_a_backup_brings_the_data_back(tmp_path):
    """Rehearsal: a copy taken now still holds data that is deleted afterwards."""
    path = in_app(backup.create_backup)
    expected = product_count(path)
    with app.app_context():
        db = store.get_db()
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DELETE FROM products")
        db.commit()
        db.execute("PRAGMA foreign_keys=ON")
    restored = tmp_path / "restored.db"
    restored.write_bytes(path.read_bytes())
    assert expected > 0
    assert product_count(restored) == expected


def test_only_the_newest_copies_are_kept(backup_folder):
    paths = [in_app(backup.create_backup) for _ in range(backup.BACKUP_KEEP + 2)]
    remaining = sorted(p.name for p in backup_folder.iterdir())
    # Copies made within the same second get -2, -3 … and still sort by age
    assert len(remaining) == backup.BACKUP_KEEP
    assert {p.name for p in paths[-backup.BACKUP_KEEP:]} == set(remaining)
    listed = in_app(backup.list_backups)
    assert listed[0]["name"] == paths[-1].name
    assert all(item["size"] > 0 for item in listed)


def test_daily_backup_runs_once_per_interval(backup_folder):
    first = in_app(backup.maybe_auto_backup)
    assert first is not None
    assert in_app(backup.maybe_auto_backup) is None
    later = in_app(backup.last_backup_at) + backup.AUTO_BACKUP_INTERVAL + timedelta(minutes=1)
    assert in_app(backup.maybe_auto_backup, later) is not None
    assert len(list(backup_folder.iterdir())) == 2


def test_failed_automatic_backup_never_breaks_a_request(backup_folder, monkeypatch):
    def boom():
        raise backup.BackupError("disk full")

    monkeypatch.setattr(backup, "_create_locked", boom)
    assert in_app(backup.maybe_auto_backup) is None


def test_housekeeping_takes_the_daily_copy_on_traffic(client, backup_folder):
    app.config["HOUSEKEEPING_ENABLED"] = True
    app_module._housekeeping["next"] = 0.0
    try:
        assert client.get("/").status_code == 200
        assert len(list(backup_folder.iterdir())) == 1
        # Throttled: the next request does not even check again
        client.get("/products")
        assert len(list(backup_folder.iterdir())) == 1
    finally:
        app.config["HOUSEKEEPING_ENABLED"] = False
        app_module._housekeeping["next"] = 0.0


def test_admin_downloads_a_fresh_backup_and_reminder_clears(admin, backup_folder):
    assert "adm_backup_reminder" not in admin.html("/admin/dashboard")  # key is translated
    assert "No database backup has been downloaded" in admin.html("/admin/dashboard")

    r = admin.post("/admin/backups")
    assert r.status_code == 200
    assert r.mimetype == "application/vnd.sqlite3"
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.data[:16] == SQLITE_MAGIC
    assert len(list(backup_folder.iterdir())) == 1

    assert "No database backup has been downloaded" not in admin.html("/admin/dashboard")
    settings = admin.html("/admin/settings")
    assert "Database backups" in settings
    name = in_app(backup.list_backups)[0]["name"]
    assert f"/admin/backups/{name}" in settings


def test_admin_downloads_an_existing_backup(admin):
    path = in_app(backup.create_backup)
    r = admin.get(f"/admin/backups/{path.name}")
    assert r.status_code == 200
    assert r.data == path.read_bytes()


@pytest.mark.parametrize(
    "name",
    ["test.db", "..%2Ftest.db", "phoenixmarket-20260101-000000.db", "phoenixmarket-x.db"],
)
def test_download_rejects_unknown_or_unsafe_names(admin, name):
    in_app(backup.create_backup)
    assert admin.get(f"/admin/backups/{name}").status_code == 404


def test_backups_are_admin_only(client, backup_folder):
    path = in_app(backup.create_backup)
    for r in (client.get(f"/admin/backups/{path.name}"), client.post("/admin/backups")):
        assert r.status_code == 302
        assert r.headers["Location"].endswith("/admin/login")
    assert len(list(backup_folder.iterdir())) == 1


def test_manual_backup_requires_csrf(admin, backup_folder):
    r = admin.post("/admin/backups", csrf=False)
    assert r.status_code == 302
    assert not backup_folder.exists() or not list(backup_folder.iterdir())


def test_turkish_settings_page_shows_backups(make_client):
    c = make_client(locale="tr", ip="10.9.9.8")
    c.login_admin()
    html = c.html("/admin/settings")
    assert "Veritabanı yedekleri" in html
    assert "Şimdi yedekle ve indir" in html
