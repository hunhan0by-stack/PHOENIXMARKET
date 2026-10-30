"""
Release rehearsal on a COPY of a database: apply this code's schema changes,
compare row counts and smoke-test the main pages. The original file is never
opened (only copied, together with any -wal/-shm side files).

    python scripts/rehearse_migration.py                  # data/phoenixmarket.db
    python scripts/rehearse_migration.py path/to/backup.db
    python scripts/rehearse_migration.py --keep           # keep the migrated copy

Exit code 0 when everything passed. The copy is deleted afterwards (it holds
customer data) unless --keep is given.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = (
    "/", "/products", "/cart", "/contact", "/health", "/sitemap.xml", "/robots.txt",
    "/legal/privacy", "/legal/distance-sales", "/legal/returns", "/legal/cookies", "/admin/login",
)


def table_counts(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}  # nosec B608 - table names from sqlite_master
    finally:
        conn.close()


# Settings rows that start-up may add to an existing database (never change or remove)
NEW_CONFIG_KEYS = {"seed.catalog_done"}


def config_rows(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT key, value FROM app_config"))
    finally:
        conn.close()


def columns(path: Path) -> set:
    conn = sqlite3.connect(path)
    try:
        out = set()
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            out |= {f"{table}.{row[1]}" for row in conn.execute(f'PRAGMA table_info("{table}")')}
        return out
    finally:
        conn.close()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    source = Path(args[0] if args else ROOT / "data" / "phoenixmarket.db").resolve()
    if not source.is_file():
        print(f"not found: {source}")
        return 2
    work = Path(tempfile.mkdtemp(prefix="phoenixmarket-rehearsal-"))
    copy = work / "phoenixmarket.db"
    shutil.copy2(source, copy)
    for suffix in ("-wal", "-shm"):
        side = source.with_name(source.name + suffix)
        if side.exists():
            shutil.copy2(side, copy.with_name(copy.name + suffix))
    print(f"source : {source} (not opened)")
    print(f"copy   : {copy}")

    before_counts, before_columns = table_counts(copy), columns(copy)
    before_config = config_rows(copy) if "app_config" in before_counts else {}
    os.environ.update({
        "DATABASE_PATH": str(copy),
        "UPLOAD_DIR": str(work / "uploads"),
        "BACKUP_DIR": str(work / "backups"),
        "EXCHANGE_RATE_API_URL": "http://127.0.0.1:9/offline",
        "EXCHANGE_RATE_REQUEST_TIMEOUT": "0.1",
    })
    sys.path.insert(0, str(ROOT))
    sys.dont_write_bytecode = True
    from app import app  # noqa: E402  (start-up applies the schema changes)
    import services.backup as backup  # noqa: E402

    ok = True
    conn = sqlite3.connect(copy)
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    print(f"integrity: {integrity}")
    ok &= integrity == "ok"

    after_counts = table_counts(copy)
    new_tables = sorted(set(after_counts) - set(before_counts))
    added = sorted(c for c in columns(copy) - before_columns if c.split(".", 1)[0] in before_counts)
    print("\nnew tables :", ", ".join(new_tables) or "none")
    print("new columns in existing tables:", ", ".join(added) or "none")
    print("\nrow counts (before -> after):")
    after_config = config_rows(copy)
    # Settings: every existing key keeps its value; only known start-up keys may be added
    config_kept = all(after_config.get(k) == v for k, v in before_config.items())
    config_added = set(after_config) - set(before_config)
    config_ok = config_kept and config_added <= NEW_CONFIG_KEYS
    for table in sorted(before_counts):
        changed = before_counts[table] != after_counts.get(table)
        if table == "app_config":
            flag = "" if not changed and config_ok else (
                f"  (added: {', '.join(sorted(config_added))}; existing rows unchanged)" if config_ok else "  << CHANGED"
            )
            ok &= config_ok
        else:
            # visits may shrink (privacy retention); everything else must be identical
            flag = "" if not changed else ("  (retention)" if table == "visits" else "  << CHANGED")
            if changed and table != "visits":
                ok = False
        print(f"  {table:<20} {before_counts[table]:>7} -> {after_counts.get(table, 0):>7}{flag}")

    print("\npages:")
    client = app.test_client()
    for path in PAGES:
        status = client.get(path, headers={"User-Agent": "rehearsal"}).status_code
        print(f"  {status}  {path}")
        ok &= status == 200

    with app.app_context():
        made = backup.create_backup()
    restored = table_counts(made)
    print(f"\nbackup of the migrated copy: {made.name}, tables match: {restored == table_counts(copy)}")
    ok &= restored == table_counts(copy)

    print("\nRESULT:", "PASS" if ok else "FAIL")
    if "--keep" in sys.argv:
        print(f"copy kept in {work}")
    else:
        # The copy holds customer data: do not leave it lying around in the temp folder
        shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
