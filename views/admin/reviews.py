"""
Review moderation.
"""
from flask import flash, redirect, render_template, request, url_for

from services.pagination import Page
import services.store as store
from services.web import safe_next, tr_msg
from views.admin.base import bp


REVIEWS_PER_PAGE = 30


@bp.route("/admin/reviews")
def reviews():
    status = (request.args.get("status") or "").strip().lower()
    status = status if status in store.REVIEW_STATUSES else None
    page = Page.from_arg(request.args.get("page"), REVIEWS_PER_PAGE, store.count_reviews(status))
    return render_template(
        "admin_reviews.html",
        reviews=store.list_reviews_admin(status, limit=page.per_page, offset=page.offset),
        page=page,
        current_status=status or "",
        filter_args={"status": status} if status else {},
        counts={s: store.count_reviews(s) for s in store.REVIEW_STATUSES},
    )


@bp.route("/admin/reviews/<int:review_id>/status", methods=["POST"])
def set_review_status(review_id):
    status = (request.form.get("status") or "").strip().lower()
    if status in store.REVIEW_STATUSES:
        store.set_review_status(review_id, status)
        flash(tr_msg("flash_review_moderated"), "success")
    return redirect(safe_next(request.form.get("next"), url_for("admin.reviews")))


@bp.route("/admin/reviews/<int:review_id>/delete", methods=["POST"])
def delete_review(review_id):
    store.delete_review(review_id)
    flash(tr_msg("flash_review_deleted"), "info")
    return redirect(safe_next(request.form.get("next"), url_for("admin.reviews")))
