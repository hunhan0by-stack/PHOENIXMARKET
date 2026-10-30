"""Field encryption components (prepared, not active): synthetic data and isolated databases only."""
import sqlite3

import pytest

import services.crypto as crypto
from services.database import init_schema
from tests.helpers import in_app


@pytest.fixture
def cipher():
    return crypto.DataCipher([crypto.generate_key()])


def test_round_trip_and_prefix(cipher):
    token = cipher.encrypt("CODE-ÇĞİÖŞÜ-123")
    assert token.startswith("enc:v1:") and "CODE" not in token
    assert cipher.decrypt(token) == "CODE-ÇĞİÖŞÜ-123"
    assert cipher.encrypt(token) == token  # never double-encrypted
    # Fernet tokens are randomised: equal codes do not give equal ciphertexts
    assert cipher.encrypt("same") != cipher.encrypt("same")


def test_legacy_plaintext_passes_through(cipher):
    assert cipher.decrypt("plain-old-code") == "plain-old-code"
    assert cipher.decrypt(None) is None and cipher.rotate("plain") == "plain"


def test_wrong_key_and_tampering_are_detected(cipher):
    token = cipher.encrypt("secret")
    with pytest.raises(crypto.DecryptionError):
        crypto.DataCipher([crypto.generate_key()]).decrypt(token)
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(crypto.DecryptionError):
        cipher.decrypt(tampered)


def test_key_rotation(cipher):
    old_key, new_key = crypto.generate_key(), crypto.generate_key()
    old = crypto.DataCipher([old_key])
    token = old.encrypt("rotate-me")
    both = crypto.DataCipher([new_key, old_key])
    assert both.decrypt(token) == "rotate-me"  # old values still readable during rotation
    rotated = both.rotate(token)
    assert crypto.DataCipher([new_key]).decrypt(rotated) == "rotate-me"


@pytest.mark.parametrize("raw", [" , ", "not-a-key", "abc,def", crypto.generate_key() + ",broken"])
def test_bad_key_configuration_is_rejected_without_echoing_it(raw):
    # A set but unusable value fails loudly instead of silently meaning "no encryption"
    with pytest.raises(crypto.CipherConfigError) as exc:
        crypto.DataCipher.from_env({"DATA_ENCRYPTION_KEYS": raw})
    for part in raw.split(","):
        if part.strip():
            assert part.strip() not in str(exc.value)


def test_unset_keys_mean_no_cipher():
    assert crypto.DataCipher.from_env({}) is None
    assert crypto.DataCipher.from_env({"DATA_ENCRYPTION_KEYS": "  "}) is None


def test_fingerprint_detects_duplicates_without_plaintext(cipher):
    assert cipher.fingerprint("CODE-1") == cipher.fingerprint("CODE-1") != cipher.fingerprint("CODE-2")
    assert "CODE-1" not in cipher.fingerprint("CODE-1")
    other = crypto.DataCipher([crypto.generate_key()])
    assert other.fingerprint("CODE-1") != cipher.fingerprint("CODE-1")


def synthetic_db(tmp_path, monkeypatch):
    """A throw-away database with this code's schema and synthetic sensitive rows."""
    from app import app

    path = tmp_path / "synthetic.db"
    monkeypatch.setitem(app.config, "DATABASE_PATH", str(path))
    in_app(init_schema)
    conn = sqlite3.connect(path)
    now = "2026-10-30T00:00:00+00:00"
    conn.execute(
        "INSERT INTO products (id, name, slug, base_price, created_at, updated_at) VALUES (1, 'P', 'p', 1, ?, ?)",
        (now, now),
    )
    conn.executemany(
        "INSERT INTO delivery_codes (product_id, code, created_at) VALUES (1, ?, ?)",
        [("SYN-CODE-1", now), ("SYN-CODE-2", now)],
    )
    conn.execute(
        "INSERT INTO orders (id, tracking_token, user_email, full_name, total_price, created_at, updated_at) "
        "VALUES (1, 't', 'syn@x.test', 'Syn', 1, ?, ?)", (now, now),
    )
    conn.execute(
        "INSERT INTO order_deliveries (order_id, body, created_at, purge_at) VALUES (1, 'user: syn / pass: x', ?, ?)",
        (now, now),
    )
    conn.execute("INSERT INTO app_config (key, value) VALUES ('admin.totp_secret', 'JBSWY3DPEHPK3PXP')")
    conn.execute("INSERT INTO app_config (key, value) VALUES ('site_settings.support_email', 'shop@x.test')")
    conn.commit()
    return conn


def values(conn):
    return {
        "codes": [r[0] for r in conn.execute("SELECT code FROM delivery_codes ORDER BY id")],
        "body": conn.execute("SELECT body FROM order_deliveries").fetchone()[0],
        "totp": conn.execute("SELECT value FROM app_config WHERE key = 'admin.totp_secret'").fetchone()[0],
        "other": conn.execute("SELECT value FROM app_config WHERE key = 'site_settings.support_email'").fetchone()[0],
    }


def test_database_encrypt_verify_rotate_and_roll_back(tmp_path, monkeypatch):
    conn = synthetic_db(tmp_path, monkeypatch)
    before = values(conn)
    first, second = crypto.generate_key(), crypto.generate_key()
    cipher = crypto.DataCipher([first])

    counts = crypto.encrypt_database(conn, cipher)
    assert counts == {"delivery_codes.code": 2, "order_deliveries.body": 1, "app_config.value": 1}
    encrypted = values(conn)
    assert all(v.startswith("enc:v1:") for v in [*encrypted["codes"], encrypted["body"], encrypted["totp"]])
    assert encrypted["other"] == "shop@x.test"  # only the listed fields
    assert crypto.encrypt_database(conn, cipher) == {k: 0 for k in counts}  # idempotent

    rotated = crypto.DataCipher([second, first])
    crypto.rotate_database(conn, rotated)
    assert crypto.verify_database(conn, crypto.DataCipher([second]))["delivery_codes.code"] == (2, 0)

    crypto.decrypt_database(conn, crypto.DataCipher([second]))
    assert values(conn) == before


def test_failed_verification_writes_nothing(tmp_path, monkeypatch):
    conn = synthetic_db(tmp_path, monkeypatch)
    before = values(conn)
    cipher = crypto.DataCipher([crypto.generate_key()])
    original = cipher.encrypt
    calls = {"n": 0}

    def broken_encrypt(value):
        calls["n"] += 1
        return "enc:v1:garbage" if calls["n"] == 2 else original(value)

    monkeypatch.setattr(cipher, "encrypt", broken_encrypt)
    with pytest.raises(crypto.DecryptionError):
        crypto.encrypt_database(conn, cipher)
    assert values(conn) == before  # the first, valid row was rolled back too


def test_prepared_modules_are_not_wired_into_the_running_shop():
    """Prepared only: field encryption and off-site backups stay unused until the owner approves."""
    from pathlib import Path

    root = Path(crypto.__file__).resolve().parents[1]
    prepared = {"crypto.py", "offsite_backup.py"}
    users = [
        p for p in [*root.glob("views/**/*.py"), *root.glob("services/**/*.py"), root / "app.py"]
        if p.name not in prepared
        and any(m in p.read_text(encoding="utf-8") for m in ("services.crypto", "services.offsite_backup"))
    ]
    assert users == []
