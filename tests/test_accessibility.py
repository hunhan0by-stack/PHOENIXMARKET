"""Accessibility guards that run without a browser (the full axe audit is manual).

Every visible form control must have an accessible name: a <label for>, a
wrapping <label>, or aria-label. Logo/product images next to their own text
are decorative.
"""
from html.parser import HTMLParser

import pytest

import services.store as store
from tests.helpers import in_app

SKIP_TYPES = {"hidden", "submit", "button", "image", "reset"}


class FormAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.label_for = set()
        self.label_depth = 0
        self.unnamed = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(a["id"])
        if tag == "label":
            self.label_depth += 1
            if a.get("for"):
                self.label_for.add(a["for"])
        elif tag in ("input", "select", "textarea"):
            if tag == "input" and (a.get("type") or "text") in SKIP_TYPES:
                return
            if self.label_depth or a.get("aria-label") or a.get("aria-labelledby"):
                return
            self.unnamed.append((tag, a.get("name"), a.get("id")))

    def handle_endtag(self, tag):
        if tag == "label" and self.label_depth:
            self.label_depth -= 1

    def problems(self):
        missing = [c for c in self.unnamed if not c[2] or c[2] not in self.label_for]
        duplicates = sorted({i for i in self.ids if self.ids.count(i) > 1})
        return missing, duplicates


def audit(html):
    parser = FormAudit()
    parser.feed(html)
    return parser.problems()


PUBLIC = ["/", "/products", "/contact", "/signin", "/signup", "/forgot-password", "/cart"]
ADMIN = ["/admin/dashboard", "/admin/orders", "/admin/listings", "/admin/listings/2/edit",
         "/admin/settings", "/admin/content", "/admin/coupons", "/admin/categories"]


@pytest.mark.parametrize("path", PUBLIC)
def test_public_form_controls_are_labelled(client, path):
    assert audit(client.html(path)) == ([], [])


def test_checkout_and_product_page_controls_are_labelled(client):
    client.post("/cart/add/2")
    assert audit(client.html("/checkout")) == ([], [])
    slug = in_app(store.get_product, 2)["slug"]
    assert audit(client.html(f"/product/{slug}")) == ([], [])


@pytest.mark.parametrize("path", ADMIN)
def test_admin_form_controls_are_labelled(admin, path):
    assert audit(admin.html(path)) == ([], [])


def test_admin_order_page_controls_are_labelled(client, admin):
    client.post("/cart/add/2")
    client.post("/checkout", {"full_name": "A", "email": "a@x.com"})
    order_id = in_app(store.list_orders_admin)[0]["id"]
    assert audit(admin.html(f"/admin/orders/{order_id}")) == ([], [])


def test_logo_link_has_a_name_and_decorative_image(client):
    html = client.html("/")
    assert 'aria-label="PHOENIXMARKET – Home"' in html
    logo_tag = html.split('src="/static/images/logo-64.png', 1)[1].split(">", 1)[0]
    assert 'alt=""' in logo_tag and 'width="32"' in logo_tag
