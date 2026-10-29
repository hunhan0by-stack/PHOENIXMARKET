"""
Order management: list, search, CSV export, fulfillment, payments, deliveries, internal notes.
"""
import csv
import io

from flask import current_app, flash, redirect, render_template, request, Response, url_for

import services.audit as audit
import services.delivery as delivery
import services.notifications as notifications
from services.pagination import Page
import services.store as store
from services.web import tr_msg
from views.admin.base import bp, notify_back_in_stock_safely, ORDER_STATUS_CHOICES


ORDERS_PER_PAGE = 25
CSV_EXPORT_LIMIT = 10000


def order_filters_from_request():
    """(status or None, query or None) from ?status=&q=; unknown statuses are ignored."""
    status = (request.args.get("status") or "").strip().lower()
    q = (request.args.get("q") or "").strip()
    return (status if status in ORDER_STATUS_CHOICES else None), (q or None)


@bp.route("/admin/orders")
def orders():
    status, q = order_filters_from_request()
    total = store.count_orders_filtered(status, q)
    page = Page.from_arg(request.args.get("page"), ORDERS_PER_PAGE, total)
    return render_template(
        "admin_orders.html",
        orders=store.search_orders(status, q, limit=page.per_page, offset=page.offset),
        page=page,
        current_status=status or "",
        current_q=q or "",
        filter_args={k: v for k, v in (("status", status), ("q", q)) if v},
        status_counts=store.count_orders_by_status(),
        status_options=ORDER_STATUS_CHOICES,
    )


def csv_safe(value) -> str:
    """Neutralise spreadsheet formulas in customer-provided cells."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@bp.route("/admin/orders.csv")
def orders_csv():
    status, q = order_filters_from_request()
    rows = store.search_orders(status, q, limit=CSV_EXPORT_LIMIT, include_items=True)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "id", "created_at", "status", "full_name", "email", "total_usd",
        "discount_usd", "coupon", "items", "tracking_number", "customer_account", "notes",
        "paid_at", "payment_ref",
    ])
    for o in rows:
        items = "; ".join(
            f"{li['display_name']} x{li['quantity']}"
            + "".join(f" [{i['label']}: {i['value']}]" for i in li["inputs"])
            for li in o["line_items"]
        )
        writer.writerow([
            o["id"],
            o["created_at"].strftime("%Y-%m-%d %H:%M") if o["created_at"] else "",
            o["fulfillment_status"],
            csv_safe(o["full_name"]),
            csv_safe(o["user_email"]),
            f"{o['total_price']:.2f}",
            f"{o['discount_usd']:.2f}",
            o["coupon_code"] or "",
            csv_safe(items),
            csv_safe(o["tracking_number"]),
            "yes" if o["user_id"] else "no",
            csv_safe(o["notes"]),
            o["paid_at"].strftime("%Y-%m-%d %H:%M") if o["paid_at"] else "",
            csv_safe(o["payment_ref"]),
        ])
    return Response(
        "﻿" + buf.getvalue(),  # BOM so Excel detects UTF-8
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=phoenixmarket-orders.csv"},
    )


@bp.route("/admin/orders/<int:order_id>", methods=["GET", "POST"])
def order_detail(order_id):
    order = store.get_order_by_id(order_id, include_items=True)
    if not order:
        flash(tr_msg("flash_order_not_found"), "error")
        return redirect(url_for("admin.orders"))
    if request.method == "POST":
        status = (request.form.get("fulfillment_status") or "").strip().lower()
        if status not in ORDER_STATUS_CHOICES:
            status = order["fulfillment_status"]
        tracking_number = (request.form.get("tracking_number") or "").strip() or None
        carrier_note = (request.form.get("carrier_note") or "").strip() or None
        # The status shown when the form was loaded (absent for older forms)
        seen_status = (request.form.get("current_status") or "").strip().lower() or None
        try:
            changes = store.update_order_fulfillment(
                order_id,
                fulfillment_status=status,
                tracking_number=tracking_number,
                carrier_note=carrier_note,
                expected_status=seen_status,
            )
        except store.OutOfStockError as exc:
            flash(tr_msg("flash_uncancel_stock", name=exc.product_name), "error")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        except store.OrderChangedError:
            flash(tr_msg("flash_order_changed_meanwhile"), "error")
            return redirect(url_for("admin.order_detail", order_id=order_id))
        flash(tr_msg("flash_order_updated"), "success")
        if status != order["fulfillment_status"]:
            audit.admin_event("order_status_changed", f"order #{order_id}: {order['fulfillment_status']} → {status}")
        if status == "cancelled" and order["fulfillment_status"] != "cancelled":
            for product_id in {li["product_id"] for li in order["line_items"]}:
                notify_back_in_stock_safely(product_id)
        if changes and request.form.get("notify_customer"):
            try:
                notifications.send_order_update(store.get_order_by_id(order_id), changes)
                flash(tr_msg("flash_customer_notified"), "info")
            except Exception:  # noqa: BLE001 - the update itself is already saved
                current_app.logger.exception("Order update email failed for order %s", order_id)
        return redirect(url_for("admin.order_detail", order_id=order_id))
    return render_template(
        "admin_order_detail.html",
        order=order,
        events=store.list_order_events(order_id),
        deliveries=store.list_deliveries(order_id),
        delivery_max=delivery.BODY_MAX,
        delivery_retention_days=delivery.retention_days(),
        status_options=ORDER_STATUS_CHOICES,
    )


@bp.route("/admin/orders/<int:order_id>/delivery", methods=["POST"])
def deliver_order(order_id):
    """Hand over account details / codes: shown on the tracking page, never emailed."""
    order = store.get_order_by_id(order_id, include_items=False)
    if not order:
        flash(tr_msg("flash_order_not_found"), "error")
        return redirect(url_for("admin.orders"))
    back = url_for("admin.order_detail", order_id=order_id) + "#delivery"
    body = (request.form.get("delivery") or "").strip()
    if order["fulfillment_status"] == "cancelled":
        flash(tr_msg("adm_delivery_cancelled_hint"), "error")
        return redirect(back)
    if not body or len(body) > delivery.BODY_MAX:
        flash(tr_msg("flash_delivery_invalid", max=delivery.BODY_MAX), "error")
        return redirect(back)
    store.add_delivery(order_id, body, delivery.retention_days())
    audit.admin_event("order_delivered", f"order #{order_id}")  # never the delivered details
    flash(tr_msg("flash_delivery_saved"), "success")
    if request.form.get("complete") and not delivery.mark_completed(order):
        flash(tr_msg("flash_order_changed_meanwhile"), "error")
    if request.form.get("notify_customer"):
        try:
            notifications.send_delivery_ready(store.get_order_by_id(order_id))
            flash(tr_msg("flash_customer_notified"), "info")
        except Exception:  # noqa: BLE001 - the delivery itself is saved
            current_app.logger.exception("Delivery email failed for order %s", order_id)
    return redirect(back)


@bp.route("/admin/orders/<int:order_id>/payment", methods=["POST"])
def confirm_payment(order_id):
    """The payment arrived (e.g. on itemsatis): record it; a pending order moves to processing."""
    if not store.get_order_by_id(order_id, include_items=False):
        flash(tr_msg("flash_order_not_found"), "error")
        return redirect(url_for("admin.orders"))
    try:
        changes = store.record_payment(order_id, request.form.get("payment_ref"))
    except store.PaymentNotAllowedError:
        flash(tr_msg("flash_payment_order_cancelled"), "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    except store.OrderChangedError:
        flash(tr_msg("flash_order_changed_meanwhile"), "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))
    audit.admin_event("payment_confirmed", f"order #{order_id}")
    flash(tr_msg("flash_payment_recorded"), "success")
    notify = bool(request.form.get("notify_customer"))
    if changes and notify:
        try:
            notifications.send_order_update(store.get_order_by_id(order_id), changes)
            flash(tr_msg("flash_customer_notified"), "info")
        except Exception:  # noqa: BLE001 - the payment is already recorded
            current_app.logger.exception("Payment email failed for order %s", order_id)
    try:
        message, params = delivery.auto_deliver(order_id, notify)
    except Exception:  # noqa: BLE001 - the payment is already recorded
        current_app.logger.exception("Automatic delivery failed for order %s", order_id)
        message, params = "flash_auto_delivery_failed", {}
    if message:
        flash(tr_msg(message, **params), "error" if message.endswith(("_short", "_failed")) else "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.route("/admin/orders/<int:order_id>/payment/undo", methods=["POST"])
def undo_payment(order_id):
    if store.undo_payment(order_id):
        audit.admin_event("payment_undone", f"order #{order_id}")
        flash(tr_msg("flash_payment_undone"), "success")
        if store.count_codes_for_order(order_id):
            # Codes the customer may already have seen cannot be taken back
            flash(tr_msg("flash_payment_undone_codes_kept"), "info")
    return redirect(url_for("admin.order_detail", order_id=order_id))


@bp.route("/admin/orders/<int:order_id>/notes", methods=["POST"])
def add_order_note(order_id):
    if not store.get_order_by_id(order_id, include_items=False):
        flash(tr_msg("flash_order_not_found"), "error")
        return redirect(url_for("admin.orders"))
    note = (request.form.get("note") or "").strip()
    if not note:
        flash(tr_msg("flash_note_empty"), "error")
    else:
        store.add_order_internal_note(order_id, note[:2000])
        flash(tr_msg("flash_note_added"), "success")
    return redirect(url_for("admin.order_detail", order_id=order_id))
