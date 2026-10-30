# PHOENIXMARKET

A storefront for digital services (subscriptions, social media growth, accounts).
Customers browse the catalog, place an order and receive a private tracking link;
payment is completed on the shop's itemsatis listing. The shop owner runs
everything from the admin panel.

**Stack:** Python 3.11 · Flask 3 · SQLite · Jinja templates · Tailwind CSS (compiled) ·
deployed on Render with gunicorn.

## Features

- **Catalog:** categories, search (Turkish-aware), sorting, in-stock filter,
  pagination, related products, ratings, favourites.
- **Packages:** a listing can be sold in packages (e.g. 1K / 5K / 10K), each with
  its own price and stock; customers must pick one.
- **Order fields:** a listing can ask for up to 3 details at checkout (e.g.
  "Instagram username", "Post link"), text or link, required or optional. The
  answers are stored per order line and shown to the admin, in the emails, the
  CSV export and on the tracking page.
- **Delivery:** account details or codes are entered on the admin order page
  and shown only on the customer's tracking page (never emailed: the email just
  says the delivery is ready). They are removed after `DELIVERY_RETENTION_DAYS`.
- **Automatic delivery:** codes / licence keys can be uploaded per listing or
  package; confirming the payment hands the next codes to the order and
  completes it. If codes run short nothing is handed out and the admin is told.
- **Orders:** stock is reserved atomically at checkout and returned on
  cancellation; discount codes; order timeline; customers can cancel pending
  orders from their tracking page. Orders not paid within the payment window
  (default 24 hours, shown to the customer) are cancelled automatically and
  their stock and discount-code use released. Orders placed before this
  feature, or re-opened by the admin, have no deadline.
- **Accounts:** sign-up / sign-in with hashed passwords, password change, reset
  by email, order history.
- **Admin:** dashboard with 30-day sales, listings and packages, categories,
  orders (search, filters, CSV export, internal notes), coupons, review
  moderation, support inbox, email outbox, site content and settings.
- **Payments:** customers pay on itemsatis (a listing or package can have its own
  itemsatis link) and send their order number; the admin confirms the payment
  ("Payment received"), which moves the order to processing.
- **Legal:** privacy/KVKK notice, distance sales agreement, returns and cookie
  pages (TR/EN templates filled with the seller details from Settings); the
  customer's consent is required and recorded with each order. Sales stay
  closed (no new orders; existing orders unaffected) until the seller details
  and support email are complete and the admin has marked the current text
  version as reviewed; a new `LEGAL_VERSION` asks for a new review. The review
  is the owner's own confirmation, not a guarantee of legal compliance.
- **Privacy-friendly analytics:** no IP addresses or cookies; daily-rotating
  anonymous visitor ids, bots excluded, page views kept 90 days.
- **Email:** every message is stored in an outbox; sent via SMTP when configured.
  The owner is notified of new orders and support messages.
- **Languages / currencies:** English and Turkish UI; prices in TRY, USD or EUR.
- **Security:** CSRF protection, rate limits, nonce-based CSP and security headers,
  safe image uploads, open-redirect protection, checkout limits (orders per IP
  and unpaid orders per email). Optional two-step sign-in for the admin
  (authenticator app codes); admin sessions end after 12 idle hours, after 7
  days, or when the admin password or two-step setting changes. A security log
  (Admin → Settings → Two-step sign-in and security log) records sign-ins,
  payments, deliveries, code stock, backups and settings changes without
  secrets. See [docs/SECURITY_AUDIT.md](docs/SECURITY_AUDIT.md).
- **AI support chat (optional):** answers with catalog context when
  `OPENAI_API_KEY` is set; FAQ answers otherwise.

## Running locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env          # optional; adjust values (macOS/Linux: cp)
python app.py                   # http://127.0.0.1:5000
```

The first start creates `data/phoenixmarket.db` and seeds a small demo catalog.
Local admin login (only when no production environment is detected):
`phoenixmarket` / `admin` unless `ADMIN_USERNAME` / `ADMIN_PASSWORD` are set.

## Tests

```bash
python -m pytest
```

Before a release run `.\scripts\check.ps1`: the test suite plus
`scripts/rehearse_migration.py`, which applies the schema changes to a copy of
`data/phoenixmarket.db` and smoke-tests the main pages (the original file is
never opened). The step-by-step launch list (in Turkish) is
[docs/RELEASE_CHECKLIST.md](docs/RELEASE_CHECKLIST.md).

The suite runs against a throw-away database and upload folder (see
`tests/conftest.py`); it never touches `data/`.

## Styles

`static/css/app.css` is compiled Tailwind CSS and is committed, so the server
needs no Node.js or build step. Its source is `static/css/input.css` (site CSS
lives there too) plus the class names used in `templates/` and `static/js/`.

After adding or changing Tailwind classes, rebuild it with the
[Tailwind v3 standalone CLI](https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17)
(a single executable, e.g. `tailwindcss-windows-x64.exe`, no npm needed):

```bash
tailwindcss -c tailwind.config.js -i static/css/input.css -o static/css/app.css --minify
```

`tests/test_styles.py` fails when a template uses a class that is missing from
`app.css`, so a forgotten rebuild shows up in the test run.

## Configuration

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Session signing key. **Required on Render.** |
| `ITEMSATIS_REDIRECT_URL` | Where customers pay after checkout. **Required in production**, and must be an `https://` link on `itemsatis.com` or `www.itemsatis.com` (the same check applies to every itemsatis link saved in the admin panel; `services/itemsatis.py`). |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Admin login. **Required in production.** |
| `DATABASE_PATH` | SQLite file (default `data/phoenixmarket.db`). |
| `UPLOAD_DIR` | Listing images (default `static/uploads`), served from `/uploads/`. |
| `BACKUP_DIR` | Daily database copies (default: `backups/` next to `DATABASE_PATH`). |
| `DEFAULT_LOCALE` | `tr` (default) or `en`: language for first-time visitors and search engines. Each page is also reachable as `?lang=tr` / `?lang=en` (canonical + hreflang + sitemap). |
| `PENDING_ORDER_TTL_HOURS` | Hours a customer has to pay (default `24`); unpaid orders are then cancelled and their stock released. `0` turns this off. |
| `DELIVERY_RETENTION_DAYS` | Days delivered account details / codes stay on the tracking page (default `14`, at least `1`); then they are removed. |
| `OUTBOX_RETENTION_DAYS` | Days sent emails stay under Admin → Emails (default `0` = kept). |
| `TRUSTED_HOSTS` | Comma-separated host names the site answers to (e.g. `shop.example,www.shop.example`); other hosts get 400. Recommended once the domain is set. |
| `ALLOW_EPHEMERAL_DATABASE` | `1` lets the app start on Render without `DATABASE_PATH` (data lost on every deploy). Throw-away test services only. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `SMTP_STARTTLS`, `SMTP_SSL` | Email delivery. Without `SMTP_HOST` emails are only logged in Admin → Emails. |
| `OPENAI_API_KEY` | Enables AI answers in the support chat. |
| `EXCHANGE_RATE_*`, `TRY_PER_USD`, `EUR_PER_USD` | Exchange-rate source and fallbacks. |

See `.env.example` for a template.

## Deployment (Render)

`render.yaml` defines the web service (`gunicorn app:app --workers 1 --threads 4`).

- **Persistent data:** Render's filesystem is reset on every deploy. `render.yaml`
  defines a disk at `/var/data` with `DATABASE_PATH`, `UPLOAD_DIR` and
  `BACKUP_DIR` on it (paid instance types). On Render the app refuses to start
  without `DATABASE_PATH` or with an `ADMIN_PASSWORD` under 12 characters, and
  the admin dashboard shows a red warning while the database folder is not on
  a mounted disk. Verify the disk as described in
  [docs/DATA_BACKUP_RECOVERY.md](docs/DATA_BACKUP_RECOVERY.md).
- **Keep one worker:** rate limits are kept in process memory.
- Schema changes are applied automatically and non-destructively at start-up
  (`services/database.py`: new tables are created, missing columns added).
- **itemsatis links:** a saved link that fails the check (e.g. saved by an older
  version) is never shown to customers and is not deleted; the log names it at
  start-up. `flask --app app check-payment-links` lists them (changes nothing).
- **Lost the phone with the admin's authenticator app?** In a Render shell run
  `flask --app app disable-admin-2fa`, sign in with the password and set
  two-step sign-in up again.

## Backups

The full procedure (verifying the Render disk, off-site copies, RPO/RTO,
restoring) is in [docs/DATA_BACKUP_RECOVERY.md](docs/DATA_BACKUP_RECOVERY.md) (Turkish).

- **Automatic:** once a day the app writes a verified copy of the database
  (SQLite online backup, safe while the shop is running) to `BACKUP_DIR` and
  keeps the newest 7. Render web services have no scheduler, so the check rides
  on incoming requests (Render's health checks count) at most every 10 minutes.
- **Off-site copy:** those copies live on the same disk as the database and do
  not survive losing that disk. Download one at least weekly from
  Admin → Settings → Database backups; the dashboard reminds you after 7 days.
- **Manual:** `flask --app app backup` (e.g. in a Render shell) writes a copy now.
- **Keep downloaded copies private:** they contain customer data, unused code
  stock and delivered details not yet removed by `DELIVERY_RETENTION_DAYS`.

**Restoring** a backup (`phoenixmarket-YYYYMMDD-HHMMSS.db`, times in UTC):

1. Stop the service (Render: *Suspend*; locally: stop the server).
2. Keep the current database aside, e.g. rename `phoenixmarket.db` to
   `phoenixmarket.db.before-restore`, and delete `phoenixmarket.db-wal` and
   `phoenixmarket.db-shm` if present.
3. Copy the backup to the `DATABASE_PATH` location under the name
   `phoenixmarket.db`.
4. Start the service and check the admin dashboard and the latest orders.
   Orders placed after the backup was taken are not in it.

## Project layout

```
app.py                 configuration, start-up, security headers, error pages
views/                 route blueprints: shop, account, orders (cart/checkout), admin, api
services/              business logic; services/store/ holds all SQL, split by area
                       (catalog, orders, users, coupons, reviews, support, analytics,
                       settings) and re-exported as `services.store`
templates/             Jinja templates (emails in templates/emails/)
static/                CSS/JS/images
tests/                 pytest suite
scripts/               check.ps1 (pre-release checks), rehearse_migration.py
docs/                  (Turkish) RELEASE_CHECKLIST.md launch steps, SECURITY_AUDIT.md,
                       DATA_BACKUP_RECOVERY.md, INCIDENT_RESPONSE.md, PRIVACY_COMPLIANCE_GAPS.md
.github/               Dependabot and the CI checks (tests, pip-audit, bandit)
data/                  local SQLite database (not for version control)
```

`PHOENIXMARKETPLACE/` is an older copy of the project kept for reference; the
application does not use it.

## Known limitations

- SQLite with a single gunicorn worker suits a small shop; heavier traffic would
  need a different database and a shared rate-limit store.
