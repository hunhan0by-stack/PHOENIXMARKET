"""
Customer accounts: sign up / sign in / sign out, profile, password change and
order history. Passwords are stored hashed in the users table.
"""
import json
import secrets

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import services.audit as audit
import services.notifications as notifications
from services.ratelimit import RateLimiter
import services.store as store
from services.web import (
    EMAIL_RE,
    MIN_PASSWORD_LENGTH,
    client_ip,
    current_user,
    get_current_locale,
    login_customer,
    logout_admin,
    logout_customer,
    password_problem,
    tr_msg,
)

bp = Blueprint("account", __name__)

# Brute-force protection for customer sign-in (per client IP)
signin_limiter = RateLimiter(max_hits=10, window_seconds=15 * 60)
# Accounts created per client IP and hour (stops scripted sign-up floods)
signup_limiter = RateLimiter(max_hits=5, window_seconds=60 * 60)
# Password reset emails: per client IP, and per account (MAX_RESETS_PER_HOUR)
reset_request_limiter = RateLimiter(max_hits=5, window_seconds=60 * 60)
MAX_RESETS_PER_HOUR = 3
RESET_TOKEN_TTL_SECONDS = 60 * 60

# Compared against when the email is unknown, so a failed sign-in takes as long
# whether or not the account exists (no account enumeration by timing).
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(16))


@bp.route("/signin", methods=["GET", "POST"])
def signin():
    if current_user():
        return redirect(url_for("account.profile"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        limit_key = client_ip()
        wait = signin_limiter.retry_after(limit_key)
        if wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("signin.html", email=email), 429
        if not email or not password:
            flash(tr_msg("flash_email_password_required"), "error")
            return render_template("signin.html", email=email)
        user = store.get_user_by_email(email)
        password_ok = check_password_hash(
            user["password_hash"] if user else _DUMMY_PASSWORD_HASH, password
        )
        if user and password_ok:
            signin_limiter.reset(limit_key)
            login_customer(user)
            flash(tr_msg("flash_signed_in"), "success")
            return redirect(url_for("account.profile"))
        signin_limiter.hit(limit_key)
        flash(tr_msg("flash_invalid_credentials"), "error")
        return render_template("signin.html", email=email)
    return render_template("signin.html", email="")


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user():
        return redirect(url_for("account.profile"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        problem = None
        if not email or not password or not confirm:
            problem = "flash_all_fields_required"
        elif not EMAIL_RE.match(email):
            problem = "flash_invalid_email"
        else:
            problem = password_problem(password, confirm)
        limit_key = client_ip()
        wait = signup_limiter.retry_after(limit_key)
        if not problem and wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("signup.html", email=email), 429
        if not problem:
            user_id = store.create_user(email, generate_password_hash(password))
            if user_id is None:
                problem = "flash_email_taken"
            else:
                signup_limiter.hit(limit_key)
                login_customer(store.get_user(user_id))
                flash(tr_msg("flash_account_created"), "success")
                return redirect(url_for("account.profile"))
        flash(tr_msg(problem, n=MIN_PASSWORD_LENGTH), "error")
        return render_template("signup.html", email=email)
    return render_template("signup.html", email="")


@bp.route("/signout", methods=["GET", "POST"])
def signout():
    # Signing out changes state, so only a CSRF-checked POST does it; a GET
    # (old bookmark, or a forged <img src="/signout">) just goes home.
    if request.method != "POST":
        return redirect(url_for("shop.index"))
    logout_customer()
    logout_admin()
    flash(tr_msg("flash_signed_out"), "success")
    return redirect(url_for("shop.index"))


@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Email a single-use reset link. The reply never reveals whether an account exists."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        limit_key = client_ip()
        wait = reset_request_limiter.retry_after(limit_key)
        if wait:
            flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
            return render_template("forgot_password.html", email=email), 429
        reset_request_limiter.hit(limit_key)
        user = store.get_user_by_email(email) if EMAIL_RE.match(email) else None
        if user and store.count_recent_password_resets(user["id"], 3600) < MAX_RESETS_PER_HOUR:
            token = secrets.token_urlsafe(32)
            store.create_password_reset(user["id"], token, RESET_TOKEN_TTL_SECONDS)
            notifications.send_password_reset(
                user["email"],
                url_for("account.reset_password", token=token, _external=True),
                get_current_locale(),
            )
        flash(tr_msg("flash_reset_sent"), "success")
        return redirect(url_for("account.signin"))
    return render_template("forgot_password.html", email="")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    reset = store.get_valid_password_reset(token)
    if not reset:
        flash(tr_msg("flash_reset_invalid"), "error")
        return redirect(url_for("account.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        problem = password_problem(password, confirm)
        if problem:
            flash(tr_msg(problem, n=MIN_PASSWORD_LENGTH), "error")
            return render_template("reset_password.html", token=token)
        if not store.complete_password_reset(reset["id"], reset["user_id"], generate_password_hash(password)):
            flash(tr_msg("flash_reset_invalid"), "error")
            return redirect(url_for("account.forgot_password"))
        login_customer(store.get_user(reset["user_id"]))
        flash(tr_msg("flash_reset_done"), "success")
        return redirect(url_for("account.profile"))
    return render_template("reset_password.html", token=token)


@bp.route("/account")
def profile():
    user = current_user()
    if not user:
        return redirect(url_for("account.signin"))
    order_count = store.count_orders_for_user(user["id"])
    return render_template("account.html", user=user, order_count=order_count)


@bp.route("/account/password", methods=["POST"])
def change_password():
    user = current_user()
    if not user:
        return redirect(url_for("account.signin"))
    record = store.get_user(user["id"])
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")
    if not record or not check_password_hash(record["password_hash"], current):
        flash(tr_msg("flash_wrong_current_password"), "error")
    elif problem := password_problem(new, confirm):
        flash(tr_msg(problem, n=MIN_PASSWORD_LENGTH), "error")
    else:
        store.update_user_password(user["id"], generate_password_hash(new))
        # Other devices are now signed out; keep this session signed in
        login_customer(store.get_user(user["id"]))
        flash(tr_msg("flash_password_changed"), "success")
    return redirect(url_for("account.profile"))


@bp.route("/account/export")
def export_data():
    """KVKK right of access: the account's data as a JSON download."""
    user = current_user()
    if not user:
        return redirect(url_for("account.signin"))
    body = json.dumps(store.export_account_data(user["id"]), ensure_ascii=False, indent=2, default=str)
    audit.customer_event("account_exported", user["id"])
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Content-Disposition": "attachment; filename=phoenixmarket-account-data.json",
            "Cache-Control": "no-store",
        },
    )


@bp.route("/account/delete", methods=["POST"])
def delete_account():
    """KVKK right to erasure: delete the account after confirming the password."""
    user = current_user()
    if not user:
        return redirect(url_for("account.signin"))
    limit_key = client_ip()
    wait = signin_limiter.retry_after(limit_key)
    if wait:
        flash(tr_msg("flash_too_many_attempts", minutes=(wait + 59) // 60), "error")
        return redirect(url_for("account.profile"))
    record = store.get_user(user["id"])
    if not record or not check_password_hash(record["password_hash"], request.form.get("password", "")):
        signin_limiter.hit(limit_key)
        flash(tr_msg("flash_wrong_current_password"), "error")
        return redirect(url_for("account.profile"))
    if not request.form.get("confirm_delete"):
        flash(tr_msg("flash_delete_confirm_required"), "error")
        return redirect(url_for("account.profile"))
    store.delete_account(user["id"])
    audit.customer_event("account_deleted", user["id"])
    logout_customer()
    flash(tr_msg("flash_account_deleted"), "success")
    return redirect(url_for("shop.index"))


@bp.route("/account/favorites")
def favorites():
    user = current_user()
    if not user:
        return redirect(url_for("account.signin"))
    return render_template("favorites.html", products=store.list_wishlist_products(user["id"]))


@bp.route("/account/orders")
def orders():
    """Order history for signed-in users (orders placed while signed in)."""
    user = current_user()
    if not user:
        return redirect(url_for("account.signin"))
    user_orders = store.list_orders_for_user(user["id"])
    return render_template("orders.html", orders=user_orders)
