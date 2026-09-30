"""
Discount codes.
"""
from datetime import datetime, timezone
import math

from flask import flash, redirect, render_template, request, url_for

import services.coupons as coupons
import services.store as store
from services.web import tr_msg
from views.admin.base import bp


def _parse_day(raw: str, end_of_day: bool):
    """'YYYY-MM-DD' -> aware UTC datetime at the start (or end) of that day; '' -> None."""
    if not raw:
        return None
    day = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return day.replace(hour=23, minute=59, second=59) if end_of_day else day


def coupon_form_values(coupon=None) -> dict:
    """String values for the coupon form (blank defaults for a new coupon)."""
    if not coupon:
        return {"code": "", "kind": "percent", "value": "", "min_order_usd": "0",
                "max_uses": "", "starts_at": "", "expires_at": "", "active": True}
    return {
        "code": coupon["code"],
        "kind": coupon["kind"],
        "value": f"{coupon['value']:g}",
        "min_order_usd": f"{coupon['min_order_usd']:g}",
        "max_uses": "" if coupon["max_uses"] is None else str(coupon["max_uses"]),
        "starts_at": coupon["starts_at"].strftime("%Y-%m-%d") if coupon["starts_at"] else "",
        "expires_at": coupon["expires_at"].strftime("%Y-%m-%d") if coupon["expires_at"] else "",
        "active": coupon["active"],
    }


def parse_coupon_form():
    """(values for the store or None, raw form values, error_key or None)."""
    f = request.form
    raw = {
        "code": coupons.normalize_code(f.get("code")),
        "kind": (f.get("kind") or "percent").strip().lower(),
        "value": (f.get("value") or "").strip(),
        "min_order_usd": (f.get("min_order_usd") or "").strip() or "0",
        "max_uses": (f.get("max_uses") or "").strip(),
        "starts_at": (f.get("starts_at") or "").strip(),
        "expires_at": (f.get("expires_at") or "").strip(),
        "active": bool(f.get("active")),
    }
    if not coupons.CODE_RE.match(raw["code"]):
        return None, raw, "flash_coupon_code_invalid"
    if raw["kind"] not in coupons.KINDS:
        raw["kind"] = "percent"
    try:
        value = float(raw["value"])
        min_order = float(raw["min_order_usd"])
        max_uses = int(raw["max_uses"]) if raw["max_uses"] else None
    except ValueError:
        return None, raw, "flash_coupon_value_invalid"
    if (
        not math.isfinite(value) or value <= 0
        or (raw["kind"] == "percent" and value > 100)
        or not math.isfinite(min_order) or min_order < 0
        or (max_uses is not None and max_uses < 1)
    ):
        return None, raw, "flash_coupon_value_invalid"
    try:
        starts = _parse_day(raw["starts_at"], end_of_day=False)
        expires = _parse_day(raw["expires_at"], end_of_day=True)
    except ValueError:
        return None, raw, "flash_coupon_dates_invalid"
    if starts and expires and expires <= starts:
        return None, raw, "flash_coupon_dates_invalid"
    data = {
        "code": raw["code"], "kind": raw["kind"], "value": value, "min_order_usd": min_order,
        "max_uses": max_uses, "starts_at": starts, "expires_at": expires, "active": raw["active"],
    }
    return data, raw, None


@bp.route("/admin/coupons", methods=["GET", "POST"])
def coupons_list():
    form = coupon_form_values()
    if request.method == "POST":
        data, form, problem = parse_coupon_form()
        if not problem and store.create_coupon(data) is None:
            problem = "flash_coupon_code_taken"
        if not problem:
            flash(tr_msg("flash_coupon_saved"), "success")
            return redirect(url_for("admin.coupons_list"))
        flash(tr_msg(problem), "error")
    return render_template(
        "admin_coupons.html",
        coupons=store.list_coupons(),
        form=form,
        now=datetime.now(timezone.utc),
    )


@bp.route("/admin/coupons/<int:coupon_id>/edit", methods=["GET", "POST"])
def edit_coupon(coupon_id):
    coupon = store.get_coupon(coupon_id)
    if not coupon:
        flash(tr_msg("flash_coupon_not_found"), "error")
        return redirect(url_for("admin.coupons_list"))
    form = coupon_form_values(coupon)
    if request.method == "POST":
        data, form, problem = parse_coupon_form()
        if not problem and not store.update_coupon(coupon_id, data):
            problem = "flash_coupon_code_taken"
        if not problem:
            flash(tr_msg("flash_coupon_saved"), "success")
            return redirect(url_for("admin.coupons_list"))
        flash(tr_msg(problem), "error")
    return render_template("admin_coupon_edit.html", coupon=coupon, form=form)


@bp.route("/admin/coupons/<int:coupon_id>/toggle", methods=["POST"])
def toggle_coupon(coupon_id):
    coupon = store.get_coupon(coupon_id)
    if coupon:
        store.set_coupon_active(coupon_id, not coupon["active"])
        flash(tr_msg("flash_coupon_saved"), "success")
    return redirect(url_for("admin.coupons_list"))


@bp.route("/admin/coupons/<int:coupon_id>/delete", methods=["POST"])
def delete_coupon(coupon_id):
    if store.get_coupon(coupon_id):
        store.delete_coupon(coupon_id)
        flash(tr_msg("flash_coupon_deleted"), "info")
    return redirect(url_for("admin.coupons_list"))
