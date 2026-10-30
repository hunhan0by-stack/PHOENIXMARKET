"""
Customer accounts and single-use password reset tokens (only hashes stored).
"""
import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone

from services.database import get_db
from services.store.base import _parse_dt


def create_user(email: str, password_hash: str) -> int | None:
    """Insert a customer account; returns None if the email is already registered."""
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (
                email.strip().lower(),
                password_hash,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    except sqlite3.IntegrityError:
        db.rollback()
        return None
    db.commit()
    return cur.lastrowid


def get_user(user_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str):
    db = get_db()
    row = db.execute(
        "SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)
    ).fetchone()
    return dict(row) if row else None


def update_user_password(user_id: int, password_hash: str) -> None:
    """Set a new password; bumping session_version signs out every other session.

    Open reset links stop working too: a link obtained before the change (e.g.
    from a mailbox that was compromised) must not undo it.
    """
    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
        (password_hash, user_id),
    )
    db.execute(
        "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), user_id),
    )
    db.commit()


def count_users() -> int:
    db = get_db()
    return int(db.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset(user_id: int, token: str, ttl_seconds: int) -> None:
    db = get_db()
    now = datetime.now(timezone.utc)
    db.execute(
        """
        INSERT INTO password_resets (user_id, token_hash, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            user_id,
            _token_hash(token),
            (now + timedelta(seconds=ttl_seconds)).isoformat(),
            now.isoformat(),
        ),
    )
    db.commit()


def count_recent_password_resets(user_id: int, within_seconds: int) -> int:
    db = get_db()
    since = (datetime.now(timezone.utc) - timedelta(seconds=within_seconds)).isoformat()
    return int(
        db.execute(
            "SELECT COUNT(*) AS c FROM password_resets WHERE user_id = ? AND created_at >= ?",
            (user_id, since),
        ).fetchone()["c"]
    )


def get_valid_password_reset(token: str):
    """The unused, unexpired reset row for token (dict), or None."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM password_resets WHERE token_hash = ? AND used_at IS NULL",
        (_token_hash(token),),
    ).fetchone()
    if not row:
        return None
    expires = _parse_dt(row["expires_at"])
    if not expires or expires <= datetime.now(timezone.utc):
        return None
    return dict(row)


def complete_password_reset(reset_id: int, user_id: int, password_hash: str) -> bool:
    """
    Set the new password, burn every open reset link of the user and sign out
    their other sessions. Returns False (changing nothing) when this link was
    used in the meantime, so a link works exactly once even under double submits.
    """
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    claimed = db.execute(
        "UPDATE password_resets SET used_at = ? WHERE id = ? AND user_id = ? AND used_at IS NULL",
        (now, reset_id, user_id),
    ).rowcount
    if claimed != 1:
        db.rollback()
        return False
    db.execute(
        "UPDATE users SET password_hash = ?, session_version = session_version + 1 WHERE id = ?",
        (password_hash, user_id),
    )
    db.execute(
        "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL",
        (now, user_id),
    )
    db.commit()
    return True
