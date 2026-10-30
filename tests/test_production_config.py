"""Production start-up checks, storage status and host handling behind the proxy."""
import re
import sqlite3
from pathlib import Path

from werkzeug.middleware.proxy_fix import ProxyFix

import app as app_module
from app import app
import services.config as config_checks
import services.store as store
from tests.helpers import in_app

GOOD = {
    "DATABASE_PATH": "/var/data/phoenixmarket.db",
    "ADMIN_PASSWORD": "a-long-admin-pass",
    "SECRET_KEY": "x" * 48,
}


def test_render_without_persistent_database_path_is_refused():
    problems = config_checks.production_problems({**GOOD, "DATABASE_PATH": ""}, on_render=True)
    assert len(problems) == 1 and "DATABASE_PATH" in problems[0]
    # Explicit opt-out for throw-away services only
    assert config_checks.production_problems(
        {**GOOD, "DATABASE_PATH": "", "ALLOW_EPHEMERAL_DATABASE": "1"}, on_render=True
    ) == []
    # Off Render (FLASK_ENV=production locally) the default data folder is fine
    assert config_checks.production_problems({**GOOD, "DATABASE_PATH": ""}, on_render=False) == []


def test_short_admin_password_is_refused():
    problems = config_checks.production_problems({**GOOD, "ADMIN_PASSWORD": "short-pass"}, on_render=True)
    assert problems and "ADMIN_PASSWORD" in problems[0]
    assert config_checks.production_problems(GOOD, on_render=True) == []


def test_missing_or_short_secret_key_is_refused_everywhere():
    """Also off Render (FLASK_ENV=production), where the dev default used to apply."""
    for on_render in (True, False):
        for key in ("", "too-short-secret"):
            problems = config_checks.production_problems({**GOOD, "SECRET_KEY": key}, on_render=on_render)
            assert len(problems) == 1 and "SECRET_KEY" in problems[0]


def test_database_and_backups_never_inside_served_folders(tmp_path):
    data, uploads, static = tmp_path / "data", tmp_path / "uploads", tmp_path / "static"
    assert config_checks.storage_layout_problems(data / "p.db", uploads, data / "backups", static) == []
    problems = config_checks.storage_layout_problems(uploads / "p.db", uploads, uploads / "backups", static)
    assert len(problems) == 2 and "DATABASE_PATH" in problems[0] and "BACKUP_DIR" in problems[1]
    assert config_checks.storage_layout_problems(data / "p.db", uploads, static / "b", static)


def test_misplaced_upload_folder_stops_start_up(tmp_path):
    """The real start-up refuses a layout that would serve the database (separate process)."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "DATABASE_PATH": str(tmp_path / "phoenixmarket.db"), "UPLOAD_DIR": str(tmp_path)}
    result = subprocess.run(
        [sys.executable, "-c", "import app"], cwd=str(Path(app_module.__file__).parent),
        env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode != 0 and "Unsafe storage layout" in result.stderr


def test_disk_space_warning(admin, monkeypatch):
    monkeypatch.setattr(config_checks, "disk_space", lambda _p: (50 * 1024 * 1024, 1000 * 1024 * 1024))
    assert "Low disk space: 50 MB free" in admin.html("/admin/dashboard")
    monkeypatch.setattr(config_checks, "disk_space", lambda _p: (800 * 1024 * 1024, 1000 * 1024 * 1024))
    assert "Low disk space" not in admin.html("/admin/dashboard")


def test_health_reports_a_broken_database(client, monkeypatch):
    assert client.get("/health").get_json() == {"status": "ok"}

    class Broken:
        def execute(self, *_a):
            raise sqlite3.OperationalError("disk I/O error at /var/data/phoenixmarket.db")

    monkeypatch.setattr(store, "get_db", lambda: Broken())
    r = client.get("/health")
    assert r.status_code == 503 and r.get_json() == {"status": "error"}
    assert "phoenixmarket.db" not in r.get_data(as_text=True)


def test_trusted_hosts_parsing():
    assert config_checks.trusted_hosts(None) is None
    assert config_checks.trusted_hosts(" Shop.Test , www.shop.test,") == ["shop.test", "www.shop.test"]


def test_storage_status_detects_a_mounted_disk(monkeypatch, tmp_path):
    db = tmp_path / "disk" / "phoenixmarket.db"
    mounted = str((tmp_path / "disk").resolve())
    monkeypatch.setattr(config_checks.os.path, "ismount", lambda p: str(p) == mounted)
    assert config_checks.storage_status(str(db), on_render=True) == {
        "database_path": str(db), "mount": mounted, "persistent": True,
    }
    monkeypatch.setattr(config_checks.os.path, "ismount", lambda p: False)
    assert config_checks.storage_status(str(db), on_render=True)["persistent"] is False
    assert config_checks.storage_status(str(db), on_render=False)["persistent"] is None


def test_dashboard_warns_when_data_is_not_persistent(admin, monkeypatch):
    monkeypatch.setitem(app.config, "STORAGE_STATUS", {"database_path": "/x.db", "mount": None, "persistent": False})
    assert "Data loss risk" in admin.html("/admin/dashboard")
    monkeypatch.setitem(app.config, "STORAGE_STATUS", {"database_path": "/x.db", "mount": "/var/data", "persistent": True})
    assert "Data loss risk" not in admin.html("/admin/dashboard")


def test_forwarded_host_cannot_poison_reset_links(client, monkeypatch):
    """H1: with the production proxy settings a spoofed X-Forwarded-Host is ignored."""
    in_app(store.create_user, "victim@x.com", "unused-hash")
    monkeypatch.setattr(app, "wsgi_app", ProxyFix(app.wsgi_app, **app_module.PROXY_FIX))
    client.post(
        "/forgot-password", {"email": "victim@x.com"},
        headers={"X-Forwarded-Host": "evil.test", "X-Forwarded-Prefix": "/evil", "X-Forwarded-Proto": "https"},
    )
    [email] = [e for e in in_app(store.list_emails) if e["kind"] == "password_reset"]
    link = re.search(r"https?://\S+/reset-password/\S+", email["body_text"]).group(0)
    assert "evil" not in link and link.startswith("https://localhost/reset-password/")


def test_untrusted_host_is_rejected_when_allow_list_is_set(client, monkeypatch):
    monkeypatch.setitem(app.config, "TRUSTED_HOSTS", ["shop.test"])
    assert client.get("/", headers={"Host": "evil.test"}).status_code == 400
    assert client.get("/", headers={"Host": "shop.test"}).status_code == 200
