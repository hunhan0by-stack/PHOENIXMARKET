"""
Off-site backups: an encrypted copy of a database backup on storage that
does not share the Render disk (docs/DATA_BACKUP_RECOVERY.md §3.1).

PREPARED, NOT ACTIVE. No target is configured and nothing in the running
shop calls this module; turning it on needs a storage account (R2, B2 or S3)
and the owner's approval.

- Provider-independent: a target only needs put/get/names/delete. Tests and
  a mounted drive use LocalDirectoryTarget; an S3-compatible target (R2, B2,
  S3 all speak the S3 API) is a ~30-line class over boto3, added with the
  chosen provider.
- Encrypted before it leaves the server (services/crypto.DataCipher, keys in
  OFFSITE_BACKUP_KEYS, separate from the field-encryption keys), so the
  storage provider never holds readable customer data.
- Every upload is read back and compared; a restore decrypts, checks the
  SQLite integrity and refuses to overwrite an existing file.
"""
import hashlib
import re
import sqlite3
from pathlib import Path

from services.crypto import DataCipher

KEYS_ENV = "OFFSITE_BACKUP_KEYS"
SUFFIX = ".enc"
SQLITE_MAGIC = b"SQLite format 3\x00"
# Only names produced by services.backup (+ SUFFIX) are accepted
NAME_RE = re.compile(r"phoenixmarket-\d{8}-\d{6}(?:-\d+)?\.db\.enc")


class OffsiteBackupError(Exception):
    """Upload verification, download or restore failed (never includes data or keys)."""


class LocalDirectoryTarget:
    """A folder as backup target: for tests, or a drive mounted from another machine."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        if not NAME_RE.fullmatch(name):
            raise OffsiteBackupError("unexpected backup name")
        return self.folder / name

    def put(self, name: str, data: bytes) -> None:
        part = self._path(name).with_suffix(".part")
        part.write_bytes(data)
        part.replace(self._path(name))

    def get(self, name: str) -> bytes:
        try:
            return self._path(name).read_bytes()
        except FileNotFoundError as exc:
            raise OffsiteBackupError(f"backup {name} not found") from exc

    def names(self) -> list:
        return sorted(p.name for p in self.folder.iterdir() if NAME_RE.fullmatch(p.name))

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)


def upload(backup_path, target, cipher: DataCipher) -> str:
    """Encrypt a local backup file, store it and verify the stored copy; returns its name."""
    backup_path = Path(backup_path)
    data = backup_path.read_bytes()
    if not data.startswith(SQLITE_MAGIC):
        raise OffsiteBackupError("not an SQLite backup file")
    name = backup_path.name + SUFFIX
    target.put(name, cipher.encrypt_bytes(data))
    stored = cipher.decrypt_bytes(target.get(name))
    if hashlib.sha256(stored).digest() != hashlib.sha256(data).digest():
        raise OffsiteBackupError(f"stored copy of {name} does not match the original")
    return name


def restore(name: str, target, cipher: DataCipher, destination) -> Path:
    """Download and decrypt a backup into destination (must not exist), verifying SQLite integrity."""
    destination = Path(destination)
    if destination.exists():
        raise OffsiteBackupError("destination already exists; restore into a new file")
    data = cipher.decrypt_bytes(target.get(name))
    if not data.startswith(SQLITE_MAGIC):
        raise OffsiteBackupError("decrypted file is not an SQLite database")
    part = destination.with_name(destination.name + ".part")
    part.write_bytes(data)
    try:
        conn = sqlite3.connect(part)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        part.unlink(missing_ok=True)
        raise OffsiteBackupError("restored file is damaged") from exc
    if result != "ok":
        part.unlink(missing_ok=True)
        raise OffsiteBackupError("restored file failed the integrity check")
    part.replace(destination)
    return destination


def prune(target, keep: int = 30) -> list:
    """Delete all but the newest `keep` off-site copies (names sort by time); returns deleted names."""
    names = target.names()
    old = names[:-keep] if keep > 0 else []
    for name in old:
        target.delete(name)
    return old
