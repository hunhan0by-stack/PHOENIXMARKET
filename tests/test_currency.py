"""Exchange rates: visitors never wait for the rate service."""
import threading
import time

import pytest

import services.currency as currency

LIVE = {"TRY": 40.0, "EUR": 0.9, "USD": 1.0}


@pytest.fixture(autouse=True)
def isolated_cache():
    saved = dict(currency._rates_cache)
    currency._rates_cache.update(rates=None, fetched_at=0.0, source="fallback", next_attempt=0.0, refreshing=False)
    yield
    # Let any background refresh finish before restoring
    for _ in range(100):
        if not currency._rates_cache["refreshing"]:
            break
        time.sleep(0.01)
    currency._rates_cache.clear()
    currency._rates_cache.update(saved)


def wait_until(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_first_call_fetches_and_caches(monkeypatch):
    calls = []
    monkeypatch.setattr(currency, "_fetch_live_rates", lambda: calls.append(1) or (LIVE, "live"))
    assert currency.get_rates() == LIVE
    assert currency.get_rates() == LIVE
    assert len(calls) == 1
    assert currency.convert(10, "USD", "TRY") == 400.0


def test_expired_rates_are_served_while_refreshing_in_background(monkeypatch):
    release = threading.Event()

    def slow_fetch():
        release.wait(5)
        return {"TRY": 41.0, "EUR": 0.95, "USD": 1.0}, "live"

    monkeypatch.setattr(currency, "_fetch_live_rates", slow_fetch)
    currency._rates_cache.update(rates=dict(LIVE), next_attempt=time.monotonic() - 1)

    started = time.monotonic()
    assert currency.get_rates() == LIVE          # stale value, immediately
    assert currency.get_rates() == LIVE          # one refresh at a time
    assert time.monotonic() - started < 0.5
    release.set()
    assert wait_until(lambda: currency._rates_cache["rates"]["TRY"] == 41.0)


def test_a_failing_service_is_not_retried_on_every_request(monkeypatch):
    calls = []
    monkeypatch.setattr(currency, "_fetch_live_rates", lambda: calls.append(1) or (None, "error"))
    first = currency.get_rates()
    assert first["TRY"] > 0 and currency._rates_cache["source"] == "fallback"
    for _ in range(20):
        currency.get_rates()
    assert len(calls) == 1
    assert currency._rates_cache["next_attempt"] > time.monotonic() + 200  # backs off ~5 minutes


def test_failed_refresh_keeps_the_last_good_rates(monkeypatch):
    monkeypatch.setattr(currency, "_fetch_live_rates", lambda: (None, "error"))
    currency._rates_cache.update(rates=dict(LIVE), source="live", next_attempt=time.monotonic() - 1)
    assert currency.get_rates() == LIVE
    assert wait_until(lambda: not currency._rates_cache["refreshing"])
    assert currency.get_rates() == LIVE and currency._rates_cache["source"] == "live"
