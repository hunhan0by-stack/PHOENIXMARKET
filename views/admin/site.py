"""
Site texts, settings, seller details for the legal pages, database backups.
"""
import sqlite3

from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for

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
        if updates:
            store.save_site_content(updates)
        flash(tr_msg("flash_content_updated"), "success")
        return redirect(url_for("admin.content"))
    return render_template("admin_content.html")


@bp.route("/admin/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        itemsatis_url = (request.form.get("itemsatis_redirect_url") or "").strip()
        support_email = (request.form.get("support_email") or "").strip()
        support_discord = (request.form.get("support_discord") or "").strip()
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

        store.save_site_settings(merged_itemsatis, merged_email, merged_discord)
        if merged_itemsatis:
            current_app.config["ITEMSATIS_REDIRECT_URL"] = merged_itemsatis

        flash(tr_msg("flash_settings_updated"), "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin_settings.html")
