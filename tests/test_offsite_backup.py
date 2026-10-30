"""Off-site backup interface (prepared, not active): a local folder stands in for R2/B2/S3."""
import sqlite3

import pytest

from app import app
import services.backup as backup
import services.crypto as crypto
import services.offsite_backup as offsite
import services.store as store
from tests.helpers import in_app


@pytest.fixture
def local_backup(tmp_path, monkeypatch):
    monkeypatch.setitem(app.config, "BACKUP_DIR", str(tmp_path / "backups"))
    in_app(store.set_config, "probe.key", "before-backup")
    return in_app(backup.create_backup)


@pytest.fixture
def cipher():
    return crypto.DataCipher([crypto.generate_key()])


def test_upload_is_encrypted_verified_and_restorable(local_backup, cipher, tmp_path):
    target = offsite.LocalDirectoryTarget(tmp_path / "offsite")
    name = offsite.upload(local_backup, target, cipher)
    assert name == local_backup.name + ".enc" and target.names() == [name]
    stored = (tmp_path / "offsite" / name).read_bytes()
    assert not stored.startswith(b"SQLite") and b"probe.key" not in stored  # provider sees ciphertext only

    restored = offsite.restore(name, target, cipher, tmp_path / "restored.db")
    conn = sqlite3.connect(restored)
    assert conn.execute("SELECT value FROM app_config WHERE key = 'probe.key'").fetchone()[0] == "before-backup"
    conn.close()


def test_restore_never_overwrites_and_rejects_wrong_keys(local_backup, cipher, tmp_path):
    target = offsite.LocalDirectoryTarget(tmp_path / "offsite")
    name = offsite.upload(local_backup, target, cipher)
    existing = tmp_path / "live.db"
    existing.write_bytes(b"live data")
    with pytest.raises(offsite.OffsiteBackupError):
        offsite.restore(name, target, cipher, existing)
    assert existing.read_bytes() == b"live data"
    with pytest.raises(crypto.DecryptionError):
        offsite.restore(name, target, crypto.DataCipher([crypto.generate_key()]), tmp_path / "x.db")


def test_damaged_or_missing_copies_are_detected(local_backup, cipher, tmp_path):
    target = offsite.LocalDirectoryTarget(tmp_path / "offsite")
    name = offsite.upload(local_backup, target, cipher)
    path = tmp_path / "offsite" / name
    data = bytearray(path.read_bytes())
    data[len(data) // 2] ^= 0x01
    path.write_bytes(bytes(data))
    with pytest.raises(crypto.DecryptionError):
        offsite.restore(name, target, cipher, tmp_path / "damaged.db")
    assert not (tmp_path / "damaged.db").exists()
    with pytest.raises(offsite.OffsiteBackupError):
        offsite.restore("phoenixmarket-20990101-000000.db.enc", target, cipher, tmp_path / "missing.db")


def test_corrupted_database_inside_valid_encryption_is_refused(cipher, tmp_path):
    target = offsite.LocalDirectoryTarget(tmp_path / "offsite")
    junk = offsite.SQLITE_MAGIC + b"\x00" * 50 + b"not really a database" * 200
    target.put("phoenixmarket-20261030-010101.db.enc", cipher.encrypt_bytes(junk))
    with pytest.raises(offsite.OffsiteBackupError):
        offsite.restore("phoenixmarket-20261030-010101.db.enc", target, cipher, tmp_path / "bad.db")
    assert not (tmp_path / "bad.db").exists() and not (tmp_path / "bad.db.part").exists()


def test_upload_verification_catches_a_faulty_provider(local_backup, cipher, tmp_path):
    class Truncating(offsite.LocalDirectoryTarget):
        def put(self, name, data):
            super().put(name, data[:-10])

    with pytest.raises((offsite.OffsiteBackupError, crypto.DecryptionError)):
        offsite.upload(local_backup, Truncating(tmp_path / "bad-provider"), cipher)


def test_only_backup_names_are_accepted(tmp_path, cipher):
    target = offsite.LocalDirectoryTarget(tmp_path / "offsite")
    for bad in ("../phoenixmarket.db", "phoenixmarket.db", "x/../../etc", "phoenixmarket-1.db.enc"):
        with pytest.raises(offsite.OffsiteBackupError):
            target.put(bad, b"x")
    not_sqlite = tmp_path / "phoenixmarket-20261030-000000.db"
    not_sqlite.write_bytes(b"hello")
    with pytest.raises(offsite.OffsiteBackupError):
        offsite.upload(not_sqlite, target, cipher)


def test_prune_keeps_the_newest(tmp_path, cipher):
    target = offsite.LocalDirectoryTarget(tmp_path / "offsite")
    names = [f"phoenixmarket-202610{d:02d}-000000.db.enc" for d in range(1, 11)]
    for n in names:
        target.put(n, b"x")
    assert offsite.prune(target, keep=3) == names[:7]
    assert target.names() == names[7:]
