"""
Data access layer (all SQL), split by area. Import the package, not the
modules: `import services.store as store` then `store.get_product(...)`.

    base       shared helpers and errors
    catalog    Listings, packages (variants) and categories; favourites and back-in-stock alerts.
    orders     Orders: checkout (atomic stock/coupon reservation), status changes, payments, sales report.
    users      Customer accounts and single-use password reset tokens (only hashes stored).
    coupons    Discount codes.
    reviews    Product reviews by customers with a completed order, and their moderation.
    support    Contact form messages with their reply history, and the email outbox.
    analytics  Privacy-friendly page-view statistics (no IPs, daily visitor ids, 90-day retention).
    privacy    KVKK self-service: account data export and account deletion.
    settings   Key/value configuration: editable site texts (EN/TR), site settings, first-run seed.
"""
from services.database import get_db  # noqa: F401  (store.get_db() is used widely)
from services.store.base import (  # noqa: F401
    CouponUnavailableError,
    LOW_STOCK_THRESHOLD,
    OrderChangedError,
    OutOfStockError,
    PaymentNotAllowedError,
    slugify,
    unique_slug,
)
from services.store.catalog import (  # noqa: F401
    EFFECTIVE_STOCK,
    PRODUCT_SELECT,
    add_stock_alert,
    category_slugs_excluding,
    count_pending_stock_alerts,
    create_category,
    create_product,
    create_variant,
    delete_category,
    delete_product,
    delete_variant,
    get_category,
    get_category_by_slug,
    get_product,
    get_product_by_slug,
    get_variant,
    list_all_products,
    list_categories,
    list_existing_slugs,
    list_low_stock_products,
    list_low_stock_variants,
    list_public_products,
    list_variants,
    list_wishlist_products,
    mark_stock_alert_notified,
    pending_stock_alerts,
    toggle_wishlist,
    update_category,
    update_product,
    update_variant,
    wishlist_product_ids,
)
from services.store.orders import add_order_internal_note, count_orders, count_orders_by_status, count_orders_filtered, count_orders_for_user, create_order, get_order_by_id, get_order_by_tracking_token, list_order_events, list_orders_admin, list_orders_for_user, record_payment, sales_report, search_orders, undo_payment, update_order_fulfillment
from services.store.users import (  # noqa: F401
    complete_password_reset,
    count_recent_password_resets,
    count_users,
    create_password_reset,
    create_user,
    get_user,
    get_user_by_email,
    get_valid_password_reset,
    update_user_password,
)
from services.store.coupons import (  # noqa: F401
    COUPON_FIELDS,
    create_coupon,
    delete_coupon,
    get_coupon,
    get_coupon_by_code,
    list_coupons,
    set_coupon_active,
    update_coupon,
)
from services.store.privacy import (  # noqa: F401
    delete_account,
    export_account_data,
)
from services.store.reviews import (  # noqa: F401
    REVIEW_STATUSES,
    count_reviews,
    delete_review,
    delete_user_review,
    get_user_review,
    list_product_reviews,
    list_reviews_admin,
    reviewer_display_name,
    save_review,
    set_review_status,
    user_can_review,
)
from services.store.support import (  # noqa: F401
    SUPPORT_STATUSES,
    count_emails,
    count_support_messages,
    create_outbox_entry,
    create_support_message,
    get_email,
    get_support_message,
    list_emails,
    list_support_messages,
    list_support_replies,
    save_support_reply,
    set_support_status,
    support_customer_context,
)
from services.store.analytics import (  # noqa: F401
    ANALYTICS_MAX_ENTRIES,
    VISIT_RETENTION_DAYS,
    anonymize_legacy_visits,
    browser_family,
    get_analytics_for_admin,
    maybe_migrate_analytics_json,
    purge_old_visits,
    record_visit,
)
from services.store.settings import (  # noqa: F401
    DEFAULT_SITE_CONTENT,
    DEFAULT_SITE_CONTENT_TR,
    SITE_CONTENT_KEYS,
    TRANSLATABLE_SITE_CONTENT_KEYS,
    get_config,
    load_site_content,
    load_site_content_tr_overrides,
    load_site_settings,
    migrate_listing_images_png_to_jpg,
    save_site_content,
    save_site_settings,
    seed_if_empty,
    set_config,
)
