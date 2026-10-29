"""Production start-up checks, storage status and host handling behind the proxy."""
import re

from werkzeug.middleware.proxy_fix import ProxyFix

import app as app_module
from app import app
import services.config as config_checks
import services.store as store
from tests.helpers import in_app

GOOD = {"DATABASE_PATH": "/var/data/phoenixmarket.db", "ADMIN_PASSWORD": "a-long-admin-pass"}


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
