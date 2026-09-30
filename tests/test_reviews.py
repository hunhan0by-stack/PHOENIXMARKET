"""Product reviews: verified-buyer rule, editing, ratings on cards and moderation."""
import pytest

import services.store as store
from tests.helpers import in_app

PRODUCT = "/product/instagram-followers"


def buy(client, admin, status="completed", name="Jane Doe"):
    """Signed-in customer orders product 2; admin moves the order to status."""
    client.post("/cart/add/2")
    client.post("/checkout", {"full_name": name, "email": "jane@x.com"})
    order_id = in_app(store.list_orders_for_user, client.user_id())[0]["id"]
    admin.post(f"/admin/orders/{order_id}", {"fulfillment_status": status})


@pytest.fixture
def buyer(make_client, admin):
    c = make_client(ip="10.40.0.1")
    c.signup(email="jane@x.com")
    buy(c, admin)
    return c


def review(client, rating=5, body="Fast delivery, works great!"):
    return client.post("/product/2/review", {"rating": str(rating), "body": body})


def test_guest_and_non_buyer_cannot_review(client, make_client, admin):
    assert "Customers who bought this service" in client.html(PRODUCT)
    assert review(client).headers["Location"].endswith("/signin")

    shopper = make_client(ip="10.40.0.2")
    shopper.signup(email="shopper@x.com")
    buy(shopper, admin, status="processing")  # not completed yet
    assert "once one of your orders for it is completed" in shopper.html(PRODUCT)
    review(shopper)
    assert any("Only customers with a completed order" in m for m in shopper.flashes())
    assert in_app(store.count_reviews) == 0


def test_buyer_reviews_and_rating_shows_everywhere(buyer, client):
    assert "Write a review" in buyer.html(PRODUCT)
    review(buyer, body="short")
    assert any("between 10 and 2000" in m for m in buyer.flashes())
    review(buyer, rating=9)
    assert in_app(store.count_reviews) == 0

    buyer.clear_flashes()
    r = review(buyer, rating=4, body="Good service, <b>fast</b> and friendly.")
    assert r.headers["Location"].endswith("#reviews")
    html = client.html(PRODUCT)
    assert "Jane D." in html and "Verified purchase" in html
    assert "&lt;b&gt;fast&lt;/b&gt;" in html  # escaped, not rendered as HTML
    assert "4.0 (1)" in client.html("/products")

    review(buyer, rating=2, body="Changed my mind, it was slow.")
    assert in_app(store.count_reviews) == 1
    product = in_app(store.get_product, 2)
    assert product["avg_rating"] == 2.0 and product["review_count"] == 1
    assert "Update your review" in buyer.html(PRODUCT)


def test_buyer_can_delete_review(buyer):
    review(buyer)
    buyer.post("/product/2/review/delete")
    assert in_app(store.count_reviews) == 0


def test_admin_moderation(buyer, admin, client):
    review(buyer)
    review_id = in_app(store.list_reviews_admin)[0]["id"]
    assert "jane@x.com" in admin.html("/admin/reviews")

    admin.post(f"/admin/reviews/{review_id}/status", {"status": "hidden", "next": "https://evil.example/"})
    assert "Jane D." not in client.html(PRODUCT)
    assert in_app(store.get_product, 2)["review_count"] == 0
    assert "currently hidden" in buyer.html(PRODUCT)
    review(buyer, body="Editing keeps it hidden.")
    assert in_app(store.get_user_review, buyer.user_id(), 2)["status"] == "hidden"

    r = admin.post(f"/admin/reviews/{review_id}/status", {"status": "published", "next": "/admin/reviews?status=hidden"})
    assert r.headers["Location"].endswith("/admin/reviews?status=hidden")
    assert "Jane D." in client.html(PRODUCT)

    r = admin.post(f"/admin/reviews/{review_id}/delete", {"next": "//evil.example"})
    assert r.headers["Location"].endswith("/admin/reviews")
    assert in_app(store.count_reviews) == 0


def test_reviewer_name_falls_back_to_email(make_client):
    c = make_client(ip="10.40.0.3")
    c.signup(email="mystery@x.com")
    assert in_app(store.reviewer_display_name, c.user_id(), "mystery@x.com") == "M***"
