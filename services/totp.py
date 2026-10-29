"""
Two-step sign-in for the admin: time-based one-time passwords (RFC 6238,
HMAC-SHA1, 30-second steps, 6 digits), as Google Authenticator, Microsoft
Authenticator, Authy and others generate them.

The secret lives in app_config once the admin has confirmed a first code.
A code is accepted one step early or late (clock drift) but only once: the
last used step is remembered, so an observed code cannot be replayed.
Recovery when the phone is lost: `flask --app app disable-admin-2fa`.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

import segno
from markupsafe import Markup

import services.store as store

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "PHOENIXMARKET"

SECRET_KEY = "admin.totp_secret"
LAST_STEP_KEY = "admin.totp_last_step"


def new_secret() -> str:
    """A random 160-bit secret in base32 (what authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def matching_step(secret: str, code: str, now: float | None = None, window: int = 1):
    """The time step whose code equals code (within ±window steps), else None."""
    code = "".join((code or "").split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    current = int((time.time() if now is None else now) // STEP_SECONDS)
    for step in range(current - window, current + window + 1):
        if hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": ISSUER})


def qr_svg(uri: str) -> Markup:
    """The otpauth link as an inline SVG QR code (dark on white, as scanners prefer)."""
    return Markup(segno.make(uri, error="m").svg_inline(scale=4, dark="#020617", light="#ffffff"))


# -----------------------------------------------------------------------------
# The admin's secret
# -----------------------------------------------------------------------------
def admin_secret():
    return store.get_config(SECRET_KEY) or None


def admin_enabled() -> bool:
    return bool(admin_secret())


def verify_admin_code(code: str, secret: str | None = None, now: float | None = None) -> bool:
    """Check a code against the admin's secret (or a secret being set up), at most once per step."""
    secret = secret or admin_secret()
    if not secret:
        return False
    step = matching_step(secret, code, now)
    if step is None or step <= int(store.get_config(LAST_STEP_KEY) or 0):
        return False
    store.set_config(LAST_STEP_KEY, str(step))
    return True


def enable_admin(secret: str) -> None:
    store.set_config(SECRET_KEY, secret)


def disable_admin() -> None:
    store.set_config(SECRET_KEY, "")
