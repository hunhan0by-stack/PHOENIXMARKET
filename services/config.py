"""
Production start-up checks and storage status.

The app refuses to start with settings that would silently lose data or
weaken admin access (a crash with a clear message in the Render log beats
discovering it after the first deploy wiped the orders).
"""
import os
from pathlib import Path

MIN_ADMIN_PASSWORD_LENGTH = 12


def production_problems(env, on_render: bool) -> list:
    """Messages for every unsafe production setting in env (a mapping like os.environ)."""
    problems = []
    if on_render and not env.get("DATABASE_PATH") and env.get("ALLOW_EPHEMERAL_DATABASE") != "1":
        problems.append(
            "DATABASE_PATH is not set: the database would live on Render's temporary filesystem "
            "and every deploy or restart would delete orders, accounts and codes. Attach a "
            "persistent disk (e.g. mounted at /var/data) and set DATABASE_PATH=/var/data/phoenixmarket.db. "
            "(ALLOW_EPHEMERAL_DATABASE=1 overrides this for throw-away test services only.)"
        )
    if len(env.get("ADMIN_PASSWORD") or "") < MIN_ADMIN_PASSWORD_LENGTH:
        problems.append(
            f"ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters in production."
        )
    return problems


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
