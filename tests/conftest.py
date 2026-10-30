"""
Shared test setup. The app reads its configuration from the environment when
app.py is imported, so everything points at a throw-away directory first.
Explicit values also win over a local .env (load_dotenv does not override).
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="phoenixmarket-tests-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.update(
    {
        "DATABASE_PATH": os.path.join(_TMP, "test.db"),
        "UPLOAD_DIR": os.path.join(_TMP, "uploads"),
        "SECRET_KEY": "test-secret",
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": "admin-pass",
        "ITEMSATIS_REDIRECT_URL": "https://itemsatis.test/shop",
        # Offline exchange rates: the static fallback is used immediately.
        "EXCHANGE_RATE_API_URL": "http://127.0.0.1:9/offline",
        "EXCHANGE_RATE_REQUEST_TIMEOUT": "0.1",
        "RENDER": "",
        "FLASK_ENV": "",
        "OPENAI_API_KEY": "",
        # The suite was written against English pages; the shop's own default
        # is Turkish (tests/test_seo_languages.py checks that separately)
        "DEFAULT_LOCALE": "en",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.helpers import ITEMSATIS_URL, Client, reset_state  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sales_closed: start without seller details or legal review (checkout is closed)",
    )


@pytest.fixture(autouse=True)
def fresh_state(request):
    """Every test starts from the seeded catalog with no users, orders or limits.

    Sales are open (sample seller details, current texts reviewed) unless the
    test is marked sales_closed, so the checkout gate can be tested on its own.
    """
    reset_state(open_sales=request.node.get_closest_marker("sales_closed") is None)
    yield


@pytest.fixture
def make_client():
    def _make(locale=None, ip="10.0.0.1"):
        return Client(locale=locale, ip=ip)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def admin(make_client):
    c = make_client(ip="10.9.9.9")
    c.login_admin()
    return c


@pytest.fixture
def itemsatis_url():
    return ITEMSATIS_URL
