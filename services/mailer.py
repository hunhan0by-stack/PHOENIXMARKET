"""
Outgoing email.

Every message is stored in the email_outbox table. When SMTP is configured
(SMTP_HOST and friends, see smtp_settings) it is also delivered in a background
thread; otherwise it stays "logged" so admins can read it under Admin -> Emails.
"""
import os
import smtplib
import sqlite3
import ssl
import threading
from datetime import datetime, timezone
from email.message import EmailMessage

from flask import current_app

import services.store as store


def _env_flag(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def smtp_settings():
    """SMTP configuration from the environment, or None when email delivery is off."""
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        return None
    user = (os.getenv("SMTP_USER") or "").strip()
    return {
        "host": host,
        "port": int(os.getenv("SMTP_PORT") or 587),
        "user": user,
        "password": os.getenv("SMTP_PASSWORD") or "",
        "use_ssl": _env_flag("SMTP_SSL", False),  # implicit TLS, usually port 465
        "starttls": _env_flag("SMTP_STARTTLS", True),
        "sender": (os.getenv("MAIL_FROM") or user or "no-reply@phoenixmarket.local").strip(),
    }


REDACTED = "[removed for security]"


def send_email(
    to_address: str, subject: str, text_body: str, html_body: str | None = None, kind: str = "",
    redact: tuple = (),
) -> int:
    """Record the message in the outbox and deliver it if SMTP is configured.

    redact: secret strings (e.g. a password-reset link) that the delivered
    email contains but the stored copy must not, so admins and database
    backups never hold a working link. Without SMTP the outbox is the only way
    the message can be read, so it is then stored as it is.
    """
    settings = smtp_settings()
    stored_text, stored_html = text_body, html_body
    if settings:
        for value in redact:
            stored_text = stored_text.replace(value, REDACTED)
            stored_html = stored_html.replace(value, REDACTED) if stored_html else stored_html
    outbox_id = store.create_outbox_entry(
        to_address, subject, stored_text, stored_html, kind, "queued" if settings else "logged"
    )
    if settings:
        message = _build_message(settings["sender"], to_address, subject, text_body, html_body)
        args = (current_app.config["DATABASE_PATH"], outbox_id, message, settings)
        if current_app.config.get("MAIL_SYNC"):
            _deliver(*args)
        else:
            threading.Thread(target=_deliver, args=args, daemon=True).start()
    return outbox_id


def _build_message(sender, to_address, subject, text_body, html_body) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def _deliver(db_path: str, outbox_id: int, message: EmailMessage, settings: dict) -> None:
    """Send via SMTP and store the outcome (runs outside the request/app context)."""
    status, error = "sent", None
    try:
        context = ssl.create_default_context()
        if settings["use_ssl"]:
            server = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=15, context=context)
        else:
            server = smtplib.SMTP(settings["host"], settings["port"], timeout=15)
        with server:
            if not settings["use_ssl"] and settings["starttls"]:
                server.starttls(context=context)
            if settings["user"]:
                server.login(settings["user"], settings["password"])
            server.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        status, error = "failed", str(exc)[:500]
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        conn.execute(
            "UPDATE email_outbox SET status = ?, error = ?, sent_at = ? WHERE id = ?",
            (status, error, datetime.now(timezone.utc).isoformat() if status == "sent" else None, outbox_id),
        )
        conn.commit()
    finally:
        conn.close()
