"""Admin listings (incl. image uploads), categories and settings."""
import io
import os
import re

import pytest
from PIL import Image

from app import app
import services.store as store
from tests.helpers import in_app, png_bytes


def listing_form(**over):
    data = {
        "name": "Test Listing",
        "description": "A test service",
        "base_price": "5",
        "base_currency": "USD",
        "listing_language": "en",
        "category": "Design",
        "stock": "7",
        "status": "active",
    }
    data.update(over)
    return data


def post_listing(admin, **over):
    return admin.post("/admin/listings/new", listing_form(**over), content_type="multipart/form-data")


def test_upload_is_saved_under_random_name_and_served(admin, client):
    r = post_listing(admin, name="Upload Test", image_file=(io.BytesIO(png_bytes()), "photo.png"))
    assert r.status_code == 302
    img = in_app(store.get_product_by_slug, "upload-test")["image_path"]
    assert re.fullmatch(r"uploads/[0-9a-f]{32}\.jpg", img)
    saved = os.path.join(app.config["UPLOAD_FOLDER"], img.split("/", 1)[1])
    with Image.open(saved) as im:
        assert im.size == (1100, 750) and im.format == "JPEG"
    r = client.get("/" + img)
    assert r.status_code == 200 and r.mimetype == "image/jpeg"
    assert "/" + img in client.html("/product/upload-test")

    post_listing(admin, name="Upload Test", image_file=(io.BytesIO(png_bytes()), "photo.png"))
    second = in_app(store.get_product_by_slug, "upload-test-2")
    assert second and second["image_path"] != img


@pytest.mark.parametrize(
    "payload,filename",
    [(b"not an image", "fake.png"), (b"GIF89a", "anim.gif")],
)
def test_bad_images_are_rejected_and_input_kept(admin, payload, filename):
    before = len(in_app(store.list_all_products))
    r = post_listing(admin, name="Broken Image", image_file=(io.BytesIO(payload), filename))
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Invalid image" in body and 'value="Broken Image"' in body
    assert len(in_app(store.list_all_products)) == before


@pytest.mark.parametrize("price", ["nan", "-5", "abc", "inf"])
def test_invalid_prices_are_rejected(admin, price):
    r = post_listing(admin, name=f"Price {price}", base_price=price)
    assert "Base price must be" in r.get_data(as_text=True)


def test_required_fields_and_status_fallback(admin):
    r = post_listing(admin, name="", description="")
    assert "are required" in r.get_data(as_text=True)
    post_listing(admin, name="Bogus Status", status="bogus")
    assert in_app(store.get_product_by_slug, "bogus-status")["status"] == "active"


def test_edit_listing_keeps_blank_stock_and_renames_slug(admin):
    post_listing(admin, name="Original")
    pid = in_app(store.get_product_by_slug, "original")["id"]
    admin.post(f"/admin/listings/{pid}/edit", listing_form(name="Renamed Listing", stock="", status="inactive"))
    p = in_app(store.get_product, pid)
    assert p["slug"] == "renamed-listing" and p["stock"] == 7 and p["status"] == "inactive"


def test_old_admin_urls_still_work(admin):
    assert admin.get("/admin/create-listing").status_code == 200
    assert admin.get("/admin/listings/edit/1").status_code == 200
    assert admin.get("/admin/edit-listing/1").status_code == 200
    r = admin.post("/admin/delete-listing/4")
    assert r.status_code == 302 and in_app(store.get_product, 4) is None


def test_oversized_upload_gets_friendly_error(admin):
    big = io.BytesIO(b"0" * (11 * 1024 * 1024))
    r = post_listing(admin, name="Too Big", image_file=(big, "big.png"))
    assert r.status_code == 302 and any("too large" in m for m in admin.flashes())


def test_category_rename_follows_listings(admin, client):
    admin.post("/admin/create-category", {"name": "Gaming"})
    cat = in_app(store.get_category_by_slug, "gaming")
    post_listing(admin, name="Game Key", category="Gaming")
    admin.post(f"/admin/edit-category/{cat['id']}", {"name": "Games", "parent_id": ""})
    assert in_app(store.get_product_by_slug, "game-key")["category"] == "Games"
    assert "Game Key" in client.html("/category/games")


def test_settings_validate_url_and_feed_homepage(admin, client):
    admin.post("/admin/settings", {"itemsatis_redirect_url": "javascript:alert(1)", "support_email": "", "support_discord": ""})
    assert any("http://" in m for m in admin.flashes())
    admin.clear_flashes()
    admin.post("/admin/settings", {"itemsatis_redirect_url": "https://itemsatis.example/shop", "support_email": "help@pm.test", "support_discord": "pm#1234"})
    html = client.html("/")
    assert "help@pm.test" in html and "pm#1234" in html


def test_dashboard_counts_customer_accounts(admin, make_client):
    make_client(ip="10.4.0.1").signup()
    assert "Customer accounts" in admin.html("/admin/dashboard")
