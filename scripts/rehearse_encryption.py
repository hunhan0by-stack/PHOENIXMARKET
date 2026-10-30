"""
Encryption rehearsal on a COPY of a database: bring the copy to this code's
schema, encrypt the sensitive fields (services/crypto.py), verify every value
decrypts to the original, rotate to a second key, decrypt again (the
rollback path) and compare with the starting values. The original file is
never opened and the copy is deleted afterwards (it holds customer data).

    python scripts/rehearse_encryption.py                  # data/phoenixmarket.db
    python scripts/rehearse_encryption.py path/to/backup.db

Throw-away keys are generated for the run and never printed or stored. This
does NOT encrypt any real database: that step waits for the owner's approval.
Exit code 0 when everything passed.
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def snapshot(conn, crypto) -> dict:
    """SHA-256 of every sensitive field value (the values themselves are never printed)."""
    digest = {}
    for table, key, column, where in crypto.FIELDS:
        for row_key, value in crypto._rows(conn, table, key, column, where):
            digest[(table, row_key)] = hashlib.sha256(str(value).encode("utf-8")).hexdigest()
    return digest


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    source = Path(args[0] if args else ROOT / "data" / "phoenixmarket.db").resolve()
    if not source.is_file():
        print(f"not found: {source}")
        return 2
    work = Path(tempfile.mkdtemp(prefix="phoenixmarket-encryption-"))
    copy = work / "phoenixmarket.db"
    shutil.copy2(source, copy)
    for suffix in ("-wal", "-shm"):
        side = source.with_name(source.name + suffix)
        if side.exists():
            shutil.copy2(side, copy.with_name(copy.name + suffix))
    print(f"source : {source} (not opened)")
    try:
        os.environ.update({
            "DATABASE_PATH": str(copy),
            "UPLOAD_DIR": str(work / "uploads"),
            "BACKUP_DIR": str(work / "backups"),
            "EXCHANGE_RATE_API_URL": "http://127.0.0.1:9/offline",
            "EXCHANGE_RATE_REQUEST_TIMEOUT": "0.1",
        })
        sys.path.insert(0, str(ROOT))
        sys.dont_write_bytecode = True
        import app  # noqa: E402,F401  (start-up brings the copy to the current schema)
        import services.crypto as crypto  # noqa: E402

        first, second = crypto.generate_key(), crypto.generate_key()
        conn = sqlite3.connect(copy)
        original = snapshot(conn, crypto)
        print(f"sensitive values in the copy: {len(original)}")

        cipher = crypto.DataCipher([first])
        print("encrypt :", crypto.encrypt_database(conn, cipher))
        report = crypto.verify_database(conn, cipher)
        ok = all(plain == 0 for _enc, plain in report.values())
        print("verify  :", report)

        rotated = crypto.DataCipher([second, first])
        print("rotate  :", crypto.rotate_database(conn, rotated))
        # After rotation the old key alone must no longer be needed
        ok &= all(plain == 0 for _e, plain in crypto.verify_database(conn, crypto.DataCipher([second])).values())

        print("rollback:", crypto.decrypt_database(conn, crypto.DataCipher([second])))
        ok &= snapshot(conn, crypto) == original
        ok &= conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        conn.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
