"""
Contact form messages with their reply history, and the email outbox.
"""
from datetime import datetime, timezone

from services.database import get_db
from services.store.base import _parse_dt
from services.store.orders import _order_from_row


SUPPORT_STATUSES = ("open", "closed")


def _support_from_row(row) -> dict:
    d = dict(row)
    d["created_at"] = _parse_dt(d["created_at"])
    d["replied_at"] = _parse_dt(d["replied_at"])
    return d


def create_support_message(*, name, email, subject, message, order_ref, user_id, locale) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO support_messages
            (name, email, subject, message, order_ref, user_id, locale, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (name, email.strip().lower(), subject, message, order_ref or None, user_id,
         locale or "en", datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return cur.lastrowid


def get_support_message(message_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM support_messages WHERE id = ?", (message_id,)).fetchone()
    return _support_from_row(row) if row else None


def count_support_messages(status: str | None = None) -> int:
    db = get_db()
    if status:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM support_messages WHERE status = ?", (status,)
        ).fetchone()
    else:
        row = db.execute("SELECT COUNT(*) AS c FROM support_messages").fetchone()
    return int(row["c"])


def list_support_messages(status: str | None = None, limit: int = 30, offset: int = 0) -> list:
    db = get_db()
    sql, params = "SELECT * FROM support_messages", []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    rows = db.execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [limit, offset]).fetchall()
    return [_support_from_row(r) for r in rows]


def save_support_reply(message_id: int, reply: str, close: bool) -> None:
    db = get_db()
    db.execute(
        "UPDATE support_messages SET admin_reply = ?, replied_at = ?, status = ? WHERE id = ?",
        (reply, datetime.now(timezone.utc).isoformat(), "closed" if close else "open", message_id),
    )
    db.commit()


def set_support_status(message_id: int, status: str) -> None:
    db = get_db()
    db.execute("UPDATE support_messages SET status = ? WHERE id = ?", (status, message_id))
    db.commit()


def create_outbox_entry(to_address, subject, body_text, body_html, kind, status) -> int:
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO email_outbox
            (to_address, subject, body_text, body_html, kind, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (to_address, subject, body_text, body_html, kind, status,
         datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    return cur.lastrowid


def _email_from_row(row) -> dict:
    d = dict(row)
    d["created_at"] = _parse_dt(d["created_at"])
    d["sent_at"] = _parse_dt(d["sent_at"])
    return d


def count_emails() -> int:
    db = get_db()
    return int(db.execute("SELECT COUNT(*) AS c FROM email_outbox").fetchone()["c"])


def list_emails(limit: int = 50, offset: int = 0) -> list:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM email_outbox ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
    ).fetchall()
    return [_email_from_row(r) for r in rows]


def get_email(email_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM email_outbox WHERE id = ?", (email_id,)).fetchone()
    return _email_from_row(row) if row else None
