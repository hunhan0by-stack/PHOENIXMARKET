"""
Order fields: what a customer must enter at checkout for a listing, e.g. the
Instagram username for a followers package or the post link for likes.

A listing keeps up to MAX_FIELDS fields (products.order_fields); the answers
are stored per order line with the label as it was at checkout
(order_items.customer_inputs), so renaming a field later never changes what
past orders show.
"""
from services.web import is_http_url

MAX_FIELDS = 3
LABEL_MAX = 60
VALUE_MAX = 300
KINDS = ("text", "url")


def parse_admin_form(form):
    """(fields, error_key or None) from the listing form's field_label_<i> / _kind_ / _required_ rows.

    fields always holds every submitted row, so the form can be shown again for correction.
    """
    fields, problem = [], None
    for i in range(MAX_FIELDS):
        label = " ".join((form.get(f"field_label_{i}") or "").split())
        if not label:
            continue
        if len(label) > LABEL_MAX:
            problem = "flash_order_fields_invalid"
        kind = form.get(f"field_kind_{i}")
        fields.append({
            "label": label,
            "kind": kind if kind in KINDS else "text",
            "required": bool(form.get(f"field_required_{i}")),
        })
    return fields, problem


def attach_to_items(items, form=None) -> None:
    """Give each checkout line its order fields with input names and (posted) values.

    Input names carry the cart key, so two lines of the same listing (two
    packages) get their own answers.
    """
    for item in items:
        item["order_fields"] = []
        for i, field in enumerate(item["product"].get("order_fields") or []):
            name = f"field-{item['key']}-{i}"
            item["order_fields"].append({
                "name": name,
                "label": field.get("label") or "",
                "kind": field.get("kind") if field.get("kind") in KINDS else "text",
                "required": bool(field.get("required")),
                "value": " ".join((form.get(name) or "").split()) if form else "",
            })


def checkout_problem(items):
    """(error_key, params) for the first missing or invalid answer, else (None, {})."""
    for item in items:
        for field in item.get("order_fields") or []:
            params = {"field": field["label"], "name": item["name"]}
            value = field["value"]
            if not value:
                if field["required"]:
                    return "flash_order_field_required", params
            elif len(value) > VALUE_MAX:
                return "flash_order_field_too_long", params
            elif field["kind"] == "url" and not is_http_url(value, VALUE_MAX):
                return "flash_order_field_url_invalid", params
    return None, {}


def answers(item) -> list:
    """The line's non-empty answers as stored with the order: [{label, value}]."""
    return [
        {"label": f["label"], "value": f["value"]}
        for f in item.get("order_fields") or [] if f["value"]
    ]
