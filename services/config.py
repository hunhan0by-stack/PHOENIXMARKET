"""
Production start-up checks, storage layout and status.

The app refuses to start with settings that would silently lose data or
weaken admin access (a crash with a clear message in the Render log beats
discovering it after the first deploy wiped the orders).
"""
import os
import shutil
from pathlib import Path

MIN_ADMIN_PASSWORD_LENGTH = 12
# Signs every session cookie: a short or guessable key lets anyone forge a
# customer session (Render's generateValue produces a long random one)
MIN_SECRET_KEY_LENGTH = 32
# Below this the dashboard warns: SQLite cannot write orders on a full disk
LOW_DISK_BYTES = 200 * 1024 * 1024


def production_problems(env, on_render: bool) -> list:
    """Messages for every unsafe production setting in env (a mapping like os.environ)."""
    problems = []
    secret_key = env.get("SECRET_KEY") or ""
    # "change-me": the placeholders of .env.example, long enough to pass the length check
    if len(secret_key) < MIN_SECRET_KEY_LENGTH or "change-me" in secret_key.lower():
        problems.append(
            f"SECRET_KEY must be a random value of at least {MIN_SECRET_KEY_LENGTH} characters in production "
            "(e.g. python -c \"import secrets; print(secrets.token_urlsafe(48))\")."
        )
    if on_render and not env.get("DATABASE_PATH") and env.get("ALLOW_EPHEMERAL_DATABASE") != "1":
        problems.append(
            "DATABASE_PATH is not set: the database would live on Render's temporary filesystem "
            "and every deploy or restart would delete orders, accounts and codes. Attach a "
            "persistent disk (e.g. mounted at /var/data) and set DATABASE_PATH=/var/data/phoenixmarket.db. "
            "(ALLOW_EPHEMERAL_DATABASE=1 overrides this for throw-away test services only.)"
        )
    admin_password = env.get("ADMIN_PASSWORD") or ""
    if len(admin_password) < MIN_ADMIN_PASSWORD_LENGTH or "change-me" in admin_password.lower():
        problems.append(
            f"ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters in production."
        )
    return problems


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
    except ValueError:
        return False
    return True


def storage_layout_problems(database_path, upload_dir, backup_dir, static_dir) -> list:
    """The database and its backups must never sit in a folder the site serves files from."""
    problems = []
    served = [("UPLOAD_DIR", Path(upload_dir)), ("static", Path(static_dir))]
    private = [("DATABASE_PATH", Path(database_path)), ("BACKUP_DIR", Path(backup_dir))]
    for private_name, private_path in private:
        for served_name, served_path in served:
            if _inside(private_path, served_path):
                problems.append(
                    f"{private_name} ({private_path}) is inside {served_name} ({served_path}), which is "
                    "served to visitors: the database or its backups could be downloaded. Use separate folders, "
                    "e.g. /var/data/phoenixmarket.db, /var/data/backups and /var/data/uploads."
                )
    return problems


def disk_space(database_path: str):
    """(free bytes, total bytes) of the disk holding the database, or None if it cannot be read."""
    try:
        usage = shutil.disk_usage(Path(database_path).resolve().parent)
    except OSError:
        return None
    return usage.free, usage.total


def trusted_hosts(raw: str | None):
    """TRUSTED_HOSTS ("shop.example, www.shop.example") as a list, or None when unset."""
    hosts = [h.strip().lower() for h in (raw or "").split(",") if h.strip()]
    return hosts or None


def mount_point(path) -> str | None:
    """The nearest mounted directory above path other than the filesystem root, or None.

    On Render a persistent disk is a separate mount (e.g. /var/data); a database
    whose folder has no such mount above it lives on the temporary filesystem.
    """
    current = Path(path).resolve().parent
    for candidate in (current, *current.parents):
        if candidate == Path(candidate.anchor):
            return None
        if os.path.ismount(candidate):
            return str(candidate)
    return None


def storage_status(database_path: str, on_render: bool) -> dict:
    """What the admin dashboard shows about where the data lives.

    persistent: True (on a separate disk), False (on Render's temporary
    filesystem) or None (not on Render: cannot be judged from here).
    """
    mount = mount_point(database_path)
    return {
        "database_path": database_path,
        "mount": mount,
        "persistent": (mount is not None) if on_render else None,
    }
