"""
Currency conversion: live rates with cache, env fallback, and static fallback.
"""
import os
import threading
import time
from typing import Any

import requests

from services.money import money

SUPPORTED_CURRENCIES = ("TRY", "USD", "EUR")
DEFAULT_CURRENCY = "USD"

# Rates relative to USD (1 USD = X TRY, 1 USD = X EUR)
_FALLBACK_TRY = float(os.getenv("TRY_PER_USD", "34.50"))
_FALLBACK_EUR = float(os.getenv("EUR_PER_USD", "0.92"))

_CACHE_TTL = int(os.getenv("EXCHANGE_RATE_CACHE_SECONDS", "3600"))
# After a failed fetch, wait this long before trying again (never per request)
_RETRY_AFTER_FAILURE = 300
_CACHE_LOCK = threading.Lock()
_rates_cache: dict[str, Any] = {
    "rates": None,
    "fetched_at": 0.0,
    "source": "fallback",
    "next_attempt": 0.0,  # monotonic time of the next fetch
    "refreshing": False,  # a background refresh is running
}

# Primary: public JSON (no API key). Override with EXCHANGE_RATE_API_URL if needed.
_DEFAULT_API = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json"
)


def _static_rates() -> dict:
    return {"TRY": _FALLBACK_TRY, "EUR": _FALLBACK_EUR, "USD": 1.0}


def _fetch_live_rates() -> tuple[dict | None, str]:
    """
    Return (rates dict or None, source label).
    Expects JSON with nested 'usd' object containing 'try' and 'eur' (lowercase keys).
    """
    url = (os.getenv("EXCHANGE_RATE_API_URL") or _DEFAULT_API).strip()
    timeout = float(os.getenv("EXCHANGE_RATE_REQUEST_TIMEOUT", "3"))
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError, TypeError):
        return None, "error"

    usd_block = data.get("usd") if isinstance(data, dict) else None
    if not isinstance(usd_block, dict):
        return None, "error"

    try:
        try_rate = float(usd_block.get("try") or usd_block.get("TRY"))
        eur_rate = float(usd_block.get("eur") or usd_block.get("EUR"))
    except (TypeError, ValueError):
        return None, "error"

    if try_rate <= 0 or eur_rate <= 0:
        return None, "error"

    return {"TRY": try_rate, "EUR": eur_rate, "USD": 1.0}, "live"


def _refresh() -> None:
    """Fetch live rates into the cache; on failure keep what we have and back off."""
    try:
        live, _src = _fetch_live_rates()
    except Exception:  # noqa: BLE001 - a refresh must never take the cache down
        live = None
    with _CACHE_LOCK:
        now = time.monotonic()
        if live is not None:
            _rates_cache.update(rates=live, fetched_at=now, source="live", next_attempt=now + _CACHE_TTL)
        else:
            _rates_cache["next_attempt"] = now + _RETRY_AFTER_FAILURE
            if _rates_cache["rates"] is None:
                _rates_cache.update(rates=_static_rates(), fetched_at=now, source="fallback")
        _rates_cache["refreshing"] = False


def get_rates() -> dict:
    """Return conversion rates: USD -> {TRY, EUR}.

    Stale-while-revalidate: once rates are cached a visitor never waits for
    the rate service; an expired cache is served while a background thread
    refreshes it. Only the very first call after start-up fetches inline.
    """
    with _CACHE_LOCK:
        cached = _rates_cache["rates"]
        if cached is not None:
            if time.monotonic() >= _rates_cache["next_attempt"] and not _rates_cache["refreshing"]:
                _rates_cache["refreshing"] = True
                threading.Thread(target=_refresh, daemon=True).start()
            return dict(cached)
    _refresh()
    with _CACHE_LOCK:
        return dict(_rates_cache["rates"])


def convert(amount: float, from_currency: str, to_currency: str) -> float:
    """
    Convert amount from from_currency to to_currency.
    Uses USD as pivot.
    """
    if not amount or from_currency == to_currency:
        return float(amount or 0)
    from_cur = (from_currency or DEFAULT_CURRENCY).upper()
    to_cur = (to_currency or DEFAULT_CURRENCY).upper()
    rates = get_rates()
    if from_cur == "USD":
        usd = amount
    elif from_cur == "TRY":
        usd = amount / rates["TRY"]
    elif from_cur == "EUR":
        usd = amount / rates["EUR"]
    else:
        usd = amount
    if to_cur == "USD":
        return money(usd)
    if to_cur == "TRY":
        return money(usd * rates["TRY"])
    if to_cur == "EUR":
        return money(usd * rates["EUR"])
    return money(usd)


def format_price(amount, currency: str = None) -> str:
    """Format price for display with currency symbol."""
    amt = float(amount or 0)
    cur = (currency or DEFAULT_CURRENCY).upper()
    symbols = {"TRY": "₺", "USD": "$", "EUR": "€"}
    sym = symbols.get(cur, cur)
    return f"{amt:,.2f} {sym}".replace(",", " ")
