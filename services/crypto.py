"""
Field encryption for the most sensitive values: code stock, delivered
details and the admin's TOTP secret.

PREPARED, NOT ACTIVE. Nothing in the running shop calls this module yet and
no real record has been encrypted. Turning it on (and migrating existing
rows) waits for the owner's approval; see
scripts/rehearse_encryption.py, which tries it on a copy of a database only.

Design
- cryptography's Fernet (AES-128-CBC + HMAC-SHA256, authenticated) through
  MultiFernet: DATA_ENCRYPTION_KEYS holds one or more keys, newest first. New
  values are encrypted with the first; any listed key decrypts. Rotation =
  put a new key first, re-encrypt (rotate_database), then drop the old key.
- Keys live only in the environment (Render → Environment and the owner's
  password manager), never in the database, its backups or the source.
- Encrypted values carry the "enc:v1:" prefix; anything else is plaintext
  from before encryption, so reading stays backward compatible and the
  migration can run (and be undone) table by table.
- fingerprint(): keyed HMAC of a code, so duplicates can be detected without
  comparing plaintext. It is derived from the first key, so rotation
  recomputes stored fingerprints.
"""
import hashlib
import hmac
import sqlite3

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "enc:v1:"
KEYS_ENV = "DATA_ENCRYPTION_KEYS"

# (table, key column, value column, extra WHERE) for every encrypted field
FIELDS = (
    ("delivery_codes", "id", "code", "code IS NOT NULL"),
    ("order_deliveries", "id", "body", "body IS NOT NULL"),
    ("app_config", "key", "value", "key IN ('admin.totp_secret', 'admin.totp_pending') AND value != ''"),
)


class CipherConfigError(ValueError):
    """DATA_ENCRYPTION_KEYS is malformed (never includes the key itself)."""


class DecryptionError(ValueError):
    """A value could not be decrypted with any configured key."""


def generate_key() -> str:
    """A new random key, to be stored in DATA_ENCRYPTION_KEYS and the password manager."""
    return Fernet.generate_key().decode("ascii")


class DataCipher:
    def __init__(self, keys: list):
        keys = [k.strip() for k in keys if k and k.strip()]
        if not keys:
            raise CipherConfigError("no encryption key given")
        try:
            fernets = [Fernet(k.encode("ascii")) for k in keys]
        except (ValueError, UnicodeEncodeError) as exc:
            raise CipherConfigError(
                "an encryption key is not a valid Fernet key (32 url-safe base64 bytes)"
            ) from exc
        self._multi = MultiFernet(fernets)
        self._fingerprint_key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"phoenixmarket fingerprint v1",
        ).derive(keys[0].encode("ascii"))

    @classmethod
    def from_env(cls, env):
        """The cipher for env[DATA_ENCRYPTION_KEYS] (comma-separated, newest first), or None when unset."""
        raw = (env.get(KEYS_ENV) or "").strip()
        return cls(raw.split(",")) if raw else None

    @staticmethod
    def is_encrypted(value) -> bool:
        return isinstance(value, str) and value.startswith(PREFIX)

    def encrypt(self, plaintext: str) -> str:
        if self.is_encrypted(plaintext):
            return plaintext  # never double-encrypt
        return PREFIX + self._multi.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, value):
        """Plaintext of value; values without the prefix (legacy plaintext, None) pass through."""
        if not self.is_encrypted(value):
            return value
        try:
            return self._multi.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise DecryptionError("value cannot be decrypted with the configured keys") from exc

    def rotate(self, value):
        """Re-encrypt an encrypted value with the newest key (plaintext passes through)."""
        if not self.is_encrypted(value):
            return value
        try:
            return PREFIX + self._multi.rotate(value[len(PREFIX):].encode("ascii")).decode("ascii")
        except InvalidToken as exc:
            raise DecryptionError("value cannot be decrypted with the configured keys") from exc

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Whole files (off-site backups): authenticated, newest key."""
        return self._multi.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        try:
            return self._multi.decrypt(token)
        except InvalidToken as exc:
            raise DecryptionError("file cannot be decrypted with the configured keys (wrong key or damaged)") from exc

    def fingerprint(self, plaintext: str) -> str:
        return hmac.new(self._fingerprint_key, plaintext.encode("utf-8"), hashlib.sha256).hexdigest()


# -----------------------------------------------------------------------------
# Whole-database operations (for the rehearsal on a copy and, once approved,
# the real migration). Each runs in one transaction and verifies before commit.
# -----------------------------------------------------------------------------
def _rows(conn, table, key, column, where):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if not exists:
        return []
    # Identifiers come from the FIELDS constant above, never from input
    return conn.execute(f"SELECT {key}, {column} FROM {table} WHERE {where}").fetchall()  # nosec B608 - identifiers from FIELDS


def _transform(conn: sqlite3.Connection, transform, check) -> dict:
    """Apply transform to every field value; check(old, new) must hold for each, else nothing is written."""
    counts = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        for table, key, column, where in FIELDS:
            changed = 0
            for row_key, value in _rows(conn, table, key, column, where):
                new = transform(value)
                if not check(value, new):
                    raise DecryptionError(f"verification failed in {table}.{column}")
                if new != value:
                    conn.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (new, row_key))  # nosec B608 - identifiers from FIELDS
                    changed += 1
            counts[f"{table}.{column}"] = changed
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def encrypt_database(conn: sqlite3.Connection, cipher: DataCipher) -> dict:
    """Encrypt every plaintext sensitive value (idempotent); each is decrypted back and compared first."""
    return _transform(
        conn, lambda v: cipher.encrypt(v) if not cipher.is_encrypted(v) else v,
        lambda old, new: cipher.decrypt(new) == cipher.decrypt(old),
    )


def decrypt_database(conn: sqlite3.Connection, cipher: DataCipher) -> dict:
    """Rollback: turn every encrypted value back into plaintext."""
    return _transform(conn, cipher.decrypt, lambda old, new: not cipher.is_encrypted(new))


def rotate_database(conn: sqlite3.Connection, cipher: DataCipher) -> dict:
    """Re-encrypt everything with the newest key (old keys may be removed afterwards)."""
    return _transform(conn, cipher.rotate, lambda old, new: cipher.decrypt(new) == cipher.decrypt(old))


def verify_database(conn: sqlite3.Connection, cipher: DataCipher) -> dict:
    """{field: (encrypted, plaintext)} counts; raises DecryptionError if any value cannot be read."""
    report = {}
    for table, key, column, where in FIELDS:
        encrypted = plain = 0
        for _key, value in _rows(conn, table, key, column, where):
            if cipher.is_encrypted(value):
                cipher.decrypt(value)
                encrypted += 1
            else:
                plain += 1
        report[f"{table}.{column}"] = (encrypted, plain)
    return report
