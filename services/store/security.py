"""
Security audit log (security_events). Nothing here deletes entries: how long
they are kept is the owner's decision.
"""
from datetime import datetime, timezone

from services.database import get_db
from services.store.base import _parse_dt

DETAIL_MAX = 300


def record_security_event(event: str, actor: str, detail: str = "", ip: str | None = None) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO security_events (event, actor, detail, ip, created_at) VALUES (?, ?, ?, ?, ?)",
        (event, actor, (detail or "")[:DETAIL_MAX], ip, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def list_security_events(limit: int = 200) -> list:
    """Newest first."""
    db = get_db()
    rows = db.execute("SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    events = []
    for r in rows:
        d = dict(r)
        d["created_at"] = _parse_dt(d["created_at"])
        events.append(d)
    return events
