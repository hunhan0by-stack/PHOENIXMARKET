"""
Admin panel ("admin.*" endpoints), split by area. Every route except login /
logout requires the admin session (base.require_admin).
"""
from views.admin.base import bp  # noqa: F401
# Importing the area modules registers their routes on bp
from views.admin import core, coupons, listings, orders, reviews, site, support  # noqa: F401,E402
