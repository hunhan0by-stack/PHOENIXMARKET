"""
Test helpers: a CSRF-aware test client and small database utilities.
Imported by conftest.py after the environment has been prepared.
"""
import io

from PIL import Image

from app import app
from services.ratelimit import RateLimiter
import services.store as store

CSRF_TOKEN = "test-csrf-token"
ITEMSATIS_URL = "https://itemsatis.test/shop"
ADMIN_LOGIN = {"username": "admin", "password": "admin-pass"}

app.config["TESTING"] = True
# Periodic maintenance (backups, order expiry) is exercised by its own tests
app.config["HOUSEKEEPING_ENABLED"] = False


def in_app(fn, *args, **kwargs):
    """Call fn inside an application context (for direct store access)."""
    with app.app_context():
        return fn(*args, **kwargs)


def reset_state():
    """Empty every table, re-seed the catalog and forget rate-limit hits."""
    app.config["ITEMSATIS_REDIRECT_URL"] = ITEMSATIS_URL
    with app.app_context():
        db = store.get_db()
        db.execute("PRAGMA foreign_keys=OFF")
        tables = [
            r["name"]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in tables:
            db.execute(f"DELETE FROM {name}")
        db.execute("DELETE FROM sqlite_sequence")
        db.commit()
        db.execute("PRAGMA foreign_keys=ON")
        store.seed_if_empty(ITEMSATIS_URL)
    RateLimiter.reset_all()


def stock_of(product_id):
    return in_app(store.get_product, product_id)["stock"]


def set_stock(product_id, stock):
    in_app(store.update_product, product_id, {"stock": stock})


def png_bytes(size=(1600, 900), color=(200, 60, 30)):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


class Client:
    """Flask test client that carries a CSRF token and a fixed client IP."""

    def __init__(self, locale=None, ip="10.0.0.1"):
        self.c = app.test_client()
        self.ip = ip
        with self.c.session_transaction() as s:
            s["_csrf_token"] = CSRF_TOKEN
            if locale:
                s["locale"] = locale

    def _kw(self, kw):
        kw.setdefault("environ_base", {"REMOTE_ADDR": self.ip})
        return kw

    def get(self, path, **kw):
        return self.c.get(path, **self._kw(kw))

    def post(self, path, data=None, csrf=True, **kw):
        data = dict(data or {})
        if csrf:
            data.setdefault("csrf_token", CSRF_TOKEN)
        if path == "/checkout":
            # Like ticking the consent box; pass accept_terms="" to leave it unticked
            data.setdefault("accept_terms", "1")
        return self.c.post(path, data=data, **self._kw(kw))

    def post_json(self, path, payload, header_token=CSRF_TOKEN, **kw):
        headers = {"X-CSRF-Token": header_token} if header_token else {}
        return self.c.post(path, json=payload, headers=headers, **self._kw(kw))

    def html(self, path, **kw):
        return self.get(path, **kw).get_data(as_text=True)

    def session(self):
        with self.c.session_transaction() as s:
            return dict(s)

    def flashes(self):
        with self.c.session_transaction() as s:
            return [m for _, m in s.get("_flashes", [])]

    def clear_flashes(self):
        with self.c.session_transaction() as s:
            s.pop("_flashes", None)

    def login_admin(self):
        r = self.post("/admin/login", ADMIN_LOGIN)
        assert r.status_code == 302 and r.headers["Location"].endswith("/admin/dashboard")
        return r

    def signup(self, email="user@x.com", password="longenough"):
        return self.post(
            "/signup",
            {"email": email, "password": password, "confirm_password": password},
        )

    def signin(self, email="user@x.com", password="longenough"):
        return self.post("/signin", {"email": email, "password": password})

    def user_id(self):
        return (self.session().get("user") or {}).get("id")
