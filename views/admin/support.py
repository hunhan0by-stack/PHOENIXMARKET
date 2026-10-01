"""
Support inbox (contact form messages) and the email outbox.
"""
from flask import current_app, flash, redirect, render_template, request, url_for

from services.mailer import smtp_settings
import services.notifications as notifications
from services.pagination import Page
import services.store as store
from services.web import tr_msg
from views.admin.base import bp


MESSAGES_PER_PAGE = 30
SUPPORT_REPLY_MAX_LENGTH = 5000


@bp.route("/admin/messages")
def messages():
    status = (request.args.get("status") or "open").strip().lower()
    status = status if status in store.SUPPORT_STATUSES or status == "all" else "open"
    filter_status = None if status == "all" else status
    page = Page.from_arg(
        request.args.get("page"), MESSAGES_PER_PAGE, store.count_support_messages(filter_status)
    )
    return render_template(
        "admin_messages.html",
        messages=store.list_support_messages(filter_status, limit=page.per_page, offset=page.offset),
        page=page,
        current_status=status,
        filter_args={"status": status},
        counts={s: store.count_support_messages(s) for s in store.SUPPORT_STATUSES},
    )


@bp.route("/admin/messages/<int:message_id>", methods=["GET", "POST"])
def message_detail(message_id):
    message = store.get_support_message(message_id)
    if not message:
        flash(tr_msg("flash_message_not_found"), "error")
        return redirect(url_for("admin.messages"))
    if request.method == "POST":
        reply = (request.form.get("reply") or "").strip()
        if not reply or len(reply) > SUPPORT_REPLY_MAX_LENGTH:
            flash(tr_msg("flash_reply_invalid"), "error")
            return redirect(url_for("admin.message_detail", message_id=message_id))
        store.save_support_reply(message_id, reply, close=bool(request.form.get("close")))
        try:
            notifications.send_support_reply(store.get_support_message(message_id))
            flash(tr_msg("flash_reply_sent"), "success")
        except Exception:  # noqa: BLE001 - the reply is stored even if email fails
            current_app.logger.exception("Support reply email failed for message %s", message_id)
        return redirect(url_for("admin.message_detail", message_id=message_id))
    return render_template(
        "admin_message_detail.html",
        message=message,
        replies=store.list_support_replies(message_id),
        context=store.support_customer_context(message["email"], message_id),
    )


@bp.route("/admin/messages/<int:message_id>/status", methods=["POST"])
def message_status(message_id):
    status = (request.form.get("status") or "").strip().lower()
    if status in store.SUPPORT_STATUSES and store.get_support_message(message_id):
        store.set_support_status(message_id, status)
    return redirect(url_for("admin.message_detail", message_id=message_id))


EMAILS_PER_PAGE = 30


@bp.route("/admin/emails")
def emails():
    page = Page.from_arg(request.args.get("page"), EMAILS_PER_PAGE, store.count_emails())
    return render_template(
        "admin_emails.html",
        emails=store.list_emails(limit=page.per_page, offset=page.offset),
        page=page,
        smtp_enabled=smtp_settings() is not None,
    )


@bp.route("/admin/emails/<int:email_id>")
def email_detail(email_id):
    email = store.get_email(email_id)
    if not email:
        flash(tr_msg("flash_email_not_found"), "error")
        return redirect(url_for("admin.emails"))
    return render_template("admin_email_detail.html", email=email)
