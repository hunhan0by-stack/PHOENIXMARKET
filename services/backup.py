"""
SQLite backups: consistent copies of the live database, taken while the shop
keeps serving requests (SQLite's online backup API, safe with WAL).

Copies are written to BACKUP_DIR (default: a ``backups`` folder next to the
database, i.e. on the same Render disk) and only the newest BACKUP_KEEP are
kept. A copy on the same disk does not survive the loss of that disk, so the
admin is reminded to download one regularly (Admin → Settings).
"""
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app

import services.store as store

BACKUP_KEEP = 7
AUTO_BACKUP_INTERVAL = timedelta(hours=24)
DOWNLOAD_REMINDER_AFTER = timedelta(days=7)
LAST_DOWNLOAD_KEY = "backup.last_download_at"

# phoenixmarket-YYYYMMDD-HHMMSS.db, plus -N when two copies share a second
BACKUP_NAME_RE = re.compile(r"^phoenixmarket-(\d{8}-\d{6})(?:-(\d+))?\.db$")

_lock = threading.Lock()


class BackupError(Exception):
    """The copy could not be written or did not pass SQLite's integrity check."""


def backup_dir() -> Path:
    configured = current_app.config.get("BACKUP_DIR")
    if configured:
        return Path(configured)
    return Path(current_app.config["DATABASE_PATH"]).parent / "backups"


def _sort_key(match):
    return match.group(1), int(match.group(2) or 1)


def _backup_files(folder: Path) -> list:
    """(match, path) for every backup in folder, oldest first."""
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        match = BACKUP_NAME_RE.match(path.name)
        if match and path.is_file():
            found.append((match, path))
    found.sort(key=lambda item: _sort_key(item[0]))
    return found


def _created_at(match) -> datetime:
    return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)


def list_backups() -> list:
    """Newest first: {name, size, created_at}."""
    return [
        {"name": path.name, "size": path.stat().st_size, "created_at": _created_at(match)}
        for match, path in reversed(_backup_files(backup_dir()))
    ]


def backup_path(name: str):
    """Path of an existing backup by file name, or None (names are never trusted as paths)."""
    if not BACKUP_NAME_RE.match(name or ""):
        return None
    path = backup_dir() / name
    return path if path.is_file() else None


def last_backup_at():
    files = _backup_files(backup_dir())
    return _created_at(files[-1][0]) if files else None


def _target_path(folder: Path, now: datetime) -> Path:
    stamp = f"{now:%Y%m%d-%H%M%S}"
    # Always above the highest number used this second (never reuse a pruned
    # name), so the newest copy also sorts as the newest
    used = [_sort_key(match)[1] for match, _ in _backup_files(folder) if match.group(1) == stamp]
    n = max(used, default=0) + 1
    return folder / (f"phoenixmarket-{stamp}.db" if n == 1 else f"phoenixmarket-{stamp}-{n}.db")


def _prune(folder: Path) -> None:
    for _, path in _backup_files(folder)[:-BACKUP_KEEP]:
        path.unlink(missing_ok=True)


def _create_locked() -> Path:
    folder = backup_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = _target_path(folder, datetime.now(timezone.utc))
    partial = target.with_name(target.name + ".part")
    try:
        source = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=30.0)
        try:
            dest = sqlite3.connect(partial)
            try:
                source.backup(dest)
                # A self-contained single file: no -wal/-shm needed to open it
                dest.execute("PRAGMA journal_mode=DELETE")
                status = dest.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                dest.close()
        finally:
            source.close()
        if status != "ok":
            raise BackupError(f"integrity check failed: {status}")
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    _prune(folder)
    return target


def create_backup() -> Path:
    """Write a verified copy of the live database now and drop the oldest extras."""
    with _lock:
        return _create_locked()


def maybe_auto_backup(now=None):
    """Daily copy, triggered by traffic (Render web services have no scheduler).

    Returns the new path, or None when a recent copy exists, another thread is
    already writing one, or the copy failed (logged; never breaks a request).
    """
    if not _lock.acquire(blocking=False):
        return None
    try:
        now = now or datetime.now(timezone.utc)
        last = last_backup_at()
        if last is not None and now - last < AUTO_BACKUP_INTERVAL:
            return None
        return _create_locked()
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Automatic database backup failed")
        return None
    finally:
        _lock.release()


def mark_downloaded(now=None) -> None:
    store.set_config(LAST_DOWNLOAD_KEY, (now or datetime.now(timezone.utc)).isoformat())


def last_download_at():
    value = store.get_config(LAST_DOWNLOAD_KEY)
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def download_overdue(now=None) -> bool:
    """True when no backup was downloaded in the last DOWNLOAD_REMINDER_AFTER."""
    last = last_download_at()
    now = now or datetime.now(timezone.utc)
    return last is None or now - last >= DOWNLOAD_REMINDER_AFTER
