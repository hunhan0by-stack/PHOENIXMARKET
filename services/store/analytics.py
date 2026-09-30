"""
Privacy-friendly page-view statistics (no IPs, daily visitor ids, 90-day retention).
"""
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.database import get_db


_analytics_lock = threading.Lock()


VISIT_RETENTION_DAYS = 90
ANALYTICS_MAX_ENTRIES = 200000


def browser_family(user_agent: str) -> str:
    """Coarse browser name; the only part of the user agent that is stored."""
    ua = (user_agent or "")[:200]
    if not ua or ua == "unknown":
        return "—"
    if "Edg" in ua:
        return "Edge"
    if "Chrome" in ua or "CriOS" in ua:
        return "Chrome"
    if "Firefox" in ua or "FxiOS" in ua:
        return "Firefox"
    if "Safari" in ua:
        return "Safari"
    return "Other"


# Visitor ids are 16 hex chars (see services.web.visitor_id); anything else in
# visits.ip is a raw address from before anonymisation.
_VISITOR_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def record_visit(path: str, visitor: str, browser: str) -> None:
    """Store a page view. visitor is a daily-rotating pseudonymous id, never an IP."""
    db = get_db()
    db.execute(
        "INSERT INTO visits (ts, path, ip, user_agent) VALUES (?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), (path or "/")[:500], visitor, browser),
    )
    db.commit()


def purge_old_visits(now: datetime | None = None) -> int:
    """Delete page views past the retention period (and beyond the row cap); returns rows removed."""
    with _analytics_lock:
        db = get_db()
        cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=VISIT_RETENTION_DAYS)).isoformat()
        removed = db.execute("DELETE FROM visits WHERE ts < ?", (cutoff,)).rowcount
        removed += db.execute(
            "DELETE FROM visits WHERE id <= (SELECT id FROM visits ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (ANALYTICS_MAX_ENTRIES,),
        ).rowcount
        db.commit()
        return removed


def anonymize_legacy_visits(make_visitor_id) -> int:
    """Replace raw IPs and full user agents stored before anonymisation (idempotent).

    make_visitor_id(ip, day) -> id, the same function used for new visits.
    """
    db = get_db()
    rows = db.execute("SELECT id, ts, ip, user_agent FROM visits").fetchall()
    changed = 0
    for row in rows:
        if _VISITOR_ID_RE.match(row["ip"] or ""):
            continue
        db.execute(
            "UPDATE visits SET ip = ?, user_agent = ? WHERE id = ?",
            (
                make_visitor_id(row["ip"] or "unknown", (row["ts"] or "")[:10]),
                browser_family(row["user_agent"]),
                row["id"],
            ),
        )
        changed += 1
    db.commit()
    return changed


def get_analytics_for_admin() -> dict:
    """Visit figures for the dashboard, computed in SQL (no full table load).

    Visitor ids rotate daily, so "visitors" counts visitor-days.
    """
    db = get_db()
    totals = db.execute(
        "SELECT COUNT(*) AS visits, COUNT(DISTINCT substr(ts, 1, 10) || ip) AS visitors FROM visits"
    ).fetchone()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_visits = db.execute(
        "SELECT COUNT(*) AS c FROM visits WHERE ts >= ?", (today,)
    ).fetchone()["c"]
    recent = [
        {
            "ts": r["ts"] or "",
            "path": r["path"] or "/",
            "visitor": (r["ip"] or "")[:6] if _VISITOR_ID_RE.match(r["ip"] or "") else "—",
            "user_agent_summary": r["user_agent"] or "—",
        }
        for r in db.execute(
            "SELECT ts, path, ip, user_agent FROM visits ORDER BY id DESC LIMIT 100"
        )
    ]
    top_pages = [
        (r["path"], r["hits"])
        for r in db.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(path), ''), '/') AS path, COUNT(*) AS hits
            FROM visits GROUP BY 1 ORDER BY hits DESC, path LIMIT 15
            """
        )
    ]
    return {
        "total_visits": int(totals["visits"]),
        "total_visitors": int(totals["visitors"]),
        "today_visits": int(today_visits),
        "recent_visits": recent,
        "top_pages": top_pages,
    }


def maybe_migrate_analytics_json(data_dir: Path) -> None:
    """One-time import from legacy analytics.json into SQLite."""
    legacy = data_dir / "analytics.json"
    if not legacy.exists():
        return
    db = get_db()
    existing = db.execute("SELECT COUNT(*) AS c FROM visits").fetchone()["c"]
    if existing > 0:
        return
    try:
        with open(legacy, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, list) or not data:
        return
    for v in data[-ANALYTICS_MAX_ENTRIES:]:
        if not isinstance(v, dict):
            continue
        db.execute(
            "INSERT INTO visits (ts, path, ip, user_agent) VALUES (?, ?, ?, ?)",
            (
                v.get("ts") or datetime.now(timezone.utc).isoformat(),
                (v.get("path") or "/")[:500],
                (v.get("ip") or "unknown")[:200],
                (v.get("user_agent") or "")[:500],
            ),
        )
    db.commit()
