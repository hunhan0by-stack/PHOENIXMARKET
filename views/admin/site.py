"""
Site texts, settings, seller details for the legal pages, database backups.
"""
import sqlite3

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for

import services.backup as backup
import services.legal as legal
import services.store as store
from services.web import SUPPORTED_LOCALES, tr_msg
from views.admin.base import bp


@bp.route("/admin/content", methods=["GET", "POST"])
def content():
    if request.method == "POST":
        updates = {}
        for key in store.SITE_CONTENT_KEYS:
            if key in request.form:
                updates[key] = request.form.get(key, "").strip()
        tr_updates = {
            key: request.form.get(f"{key}__tr", "").strip()
            for key in store.TRANSLATABLE_SITE_CONTENT_KEYS
            if f"{key}__tr" in request.form
        }
        if updates or tr_updates:
            store.save_site_content(updates, tr_updates)
        flash(tr_msg("flash_content_updated"), "success")
        return redirect(url_for("admin.content"))
    # Explicit languages: the page's own site_content follows the admin's UI language
    return render_template(
        "admin_content.html",
        content_en=store.load_site_content("en"),
        content_tr=store.load_site_content_tr_overrides(),
        content_tr_defaults=store.DEFAULT_SITE_CONTENT_TR,
    )


@bp.route("/admin/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        itemsatis_url = (request.form.get("itemsatis_redirect_url") or "").strip()
        support_email = (request.form.get("support_email") or "").strip()
        support_discord = (request.form.get("support_discord") or "").strip()
        owner_locale = request.form.get("owner_locale")
        if owner_locale not in SUPPORTED_LOCALES:
            owner_locale = None  # missing or unknown: keep the current language
        if itemsatis_url and not itemsatis_url.startswith(("http://", "https://")):
            flash(tr_msg("flash_invalid_url"), "error")
            return redirect(url_for("admin.settings"))

        cur = store.load_site_settings(
            current_app.config["ITEMSATIS_REDIRECT_URL"],
            "contact@phoenixmarket.example",
            "YourDiscordTag#0000",
        )
        merged_email = support_email or cur.get("support_email", "")
        merged_discord = support_discord or cur.get("support_discord", "")
        merged_itemsatis = itemsatis_url or cur.get("itemsatis_redirect_url", "")

        store.save_site_settings(merged_itemsatis, merged_email, merged_discord, owner_locale)
        if merged_itemsatis:
            current_app.config["ITEMSATIS_REDIRECT_URL"] = merged_itemsatis

        flash(tr_msg("flash_settings_updated"), "success")
        return redirect(url_for("admin.settings"))
    return render_template(
        "admin_settings.html",
        backups=backup.list_backups(),
        last_download=backup.last_download_at(),
        backup_overdue=backup.download_overdue(),
        seller=legal.seller_info(),
        legal_reviewed=legal.texts_reviewed(),
    )


@bp.route("/admin/settings/legal", methods=["POST"])
def legal_settings():
    """Seller details shown on the legal pages, and the 'texts reviewed' confirmation."""
    legal.save_seller_info(
        {field: request.form.get(f"seller_{field}", "") for field in legal.SELLER_FIELDS}
    )
    legal.set_texts_reviewed(bool(request.form.get("texts_reviewed")))
    flash(tr_msg("flash_settings_updated"), "success")
    return redirect(url_for("admin.settings") + "#legal")


def send_backup(path):
    backup.mark_downloaded()
    return send_file(
        path,
        mimetype="application/vnd.sqlite3",
        as_attachment=True,
        download_name=path.name,
        max_age=0,
    )


@bp.route("/admin/backups", methods=["POST"])
def create_backup():
    """Take a fresh copy of the database and download it."""
    try:
        path = backup.create_backup()
    except (backup.BackupError, OSError, sqlite3.Error):
        current_app.logger.exception("Manual database backup failed")
        flash(tr_msg("flash_backup_failed"), "error")
        return redirect(url_for("admin.settings"))
    return send_backup(path)


@bp.route("/admin/backups/<name>")
def download_backup(name):
    path = backup.backup_path(name)
    if path is None:
        abort(404)
    return send_backup(path)
