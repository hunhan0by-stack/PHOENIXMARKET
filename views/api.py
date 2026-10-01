"""
JSON API: the support chat assistant (OpenAI, server-side key).
"""
import os

from flask import Blueprint, current_app, jsonify, request
from openai import OpenAI

from services.currency import convert, format_price
from services.listing import format_product_price, get_base_currency
from services.ratelimit import RateLimiter
import services.store as store
from services.web import client_ip, get_current_currency, get_current_locale, tr_msg

bp = Blueprint("api", __name__)

CHAT_MAX_MESSAGE_LENGTH = 1000
# Protects API credits (per client IP)
chat_limiter = RateLimiter(max_hits=20, window_seconds=10 * 60)


def chat_api_key():
    """The configured OpenAI key, or None when AI chat is disabled."""
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    return key if key and key != "YOUR_API_KEY_HERE" else None


def chat_catalog_summary(limit: int = 30) -> str:
    """Active listings as short lines, so the assistant can answer catalog questions."""
    currency = get_current_currency()
    lines = []
    for p in store.list_public_products()[:limit]:
        availability = "in stock" if (p.get("stock") or 0) > 0 else "out of stock"
        if p["has_variants"]:
            packages = ", ".join(
                f"{v['name']} {format_price(convert(v['price'], get_base_currency(p), currency), currency)}"
                + ("" if v["stock"] > 0 else " (out of stock)")
                for v in store.list_variants(p["id"], active_only=True)
            )
            price = f"packages: {packages or 'none available'}"
        else:
            price = format_product_price(p, currency)
        lines.append(f"- {p['name']} ({p.get('category') or 'General'}): {price}, {availability}")
    return "\n".join(lines) or "- (no active listings)"


@bp.post("/api/chat")
def chat():
    """Server-side AI chat endpoint.

    - Reads OPENAI_API_KEY from environment (see .env).
    - Never exposes the key to the browser.
    - If no key is configured, returns a clear 503 error.
    - Rate limited per client IP to protect API credits.
    """

    data = request.get_json(silent=True)
    message = data.get("message") if isinstance(data, dict) else None
    user_message = message.strip() if isinstance(message, str) else ""

    if not user_message:
        return jsonify({"error": tr_msg("chat_error_empty")}), 400
    if len(user_message) > CHAT_MAX_MESSAGE_LENGTH:
        return jsonify(
            {"error": tr_msg("chat_error_too_long", n=CHAT_MAX_MESSAGE_LENGTH)}
        ), 400

    api_key = chat_api_key()
    if not api_key:
        # Frontend will gracefully fall back to FAQ answers.
        return jsonify({"error": tr_msg("chat_error_unavailable")}), 503

    limit_key = client_ip()
    if chat_limiter.retry_after(limit_key):
        return jsonify({"error": tr_msg("chat_error_rate_limited")}), 429
    chat_limiter.hit(limit_key)

    language = "Turkish" if get_current_locale() == "tr" else "English"
    system_prompt = (
        "You are the PHOENIXMARKET customer support assistant. "
        "Help users with sign-in, sign-up, cart, checkout, orders, account, and general marketplace questions. "
        "Be concise, professional, and use clear steps. Do not discuss internal implementation details. "
        f"Reply in {language} unless the user writes in another language. "
        "After checkout the customer gets a private tracking link and completes payment on itemsatis. "
        "Current catalog (prices in the visitor's currency):\n" + chat_catalog_summary()
    )

    try:
        client = OpenAI(api_key=api_key, timeout=20.0)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=400,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        answer = completion.choices[0].message.content.strip()
        return jsonify({"answer": answer})
    except Exception:  # noqa: BLE001 - never leak provider errors to the browser
        current_app.logger.exception("AI chat request failed")
        return jsonify({"error": tr_msg("chat_error_upstream")}), 502
