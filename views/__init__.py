"""
Route blueprints. Endpoint names are "<blueprint>.<view>", e.g. url_for("shop.products").
"""
from views import account, admin, api, orders, shop


def register_blueprints(app):
    for module in (shop, account, orders, admin, api):
        app.register_blueprint(module.bp)
