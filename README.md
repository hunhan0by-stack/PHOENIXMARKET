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
- **Orders:** stock is reserved atomically at checkout and returned on
  cancellation; discount codes; order timeline; customers can cancel pending
  orders from their tracking page.
- **Accounts:** sign-up / sign-in with hashed passwords, password change, reset
  by email, order history.
- **Admin:** dashboard with 30-day sales, listings and packages, categories,
  orders (search, filters, CSV export, internal notes), coupons, review
  moderation, support inbox, email outbox, site content and settings.
- **Email:** every message is stored in an outbox; sent via SMTP when configured.
- **Languages / currencies:** English and Turkish UI; prices in TRY, USD or EUR.
- **Security:** CSRF protection, rate limits, nonce-based CSP and security headers,
  safe image uploads, open-redirect protection.
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
| `ITEMSATIS_REDIRECT_URL` | Where customers pay after checkout. **Required on Render.** |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | Admin login. **Required in production.** |
| `DATABASE_PATH` | SQLite file (default `data/phoenixmarket.db`). |
| `UPLOAD_DIR` | Listing images (default `static/uploads`), served from `/uploads/`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `SMTP_STARTTLS`, `SMTP_SSL` | Email delivery. Without `SMTP_HOST` emails are only logged in Admin → Emails. |
| `OPENAI_API_KEY` | Enables AI answers in the support chat. |
| `EXCHANGE_RATE_*`, `TRY_PER_USD`, `EUR_PER_USD` | Exchange-rate source and fallbacks. |

See `.env.example` for a template.

## Deployment (Render)

`render.yaml` defines the web service (`gunicorn app:app --workers 1 --threads 4`).

- **Persistent data:** Render's filesystem is reset on every deploy. Attach a
  persistent disk and set `DATABASE_PATH` (e.g. `/var/data/phoenixmarket.db`)
  and `UPLOAD_DIR` (e.g. `/var/data/uploads`) to paths on it; otherwise orders,
  accounts and uploaded images are lost on redeploy.
- **Keep one worker:** rate limits are kept in process memory.
- Schema changes are applied automatically and non-destructively at start-up
  (`services/database.py`: new tables are created, missing columns added).

## Project layout

```
app.py                 configuration, start-up, security headers, error pages
views/                 route blueprints: shop, account, orders (cart/checkout), admin, api
services/              business logic and data access (store.py holds all SQL)
templates/             Jinja templates (emails in templates/emails/)
static/                CSS/JS/images
tests/                 pytest suite
data/                  local SQLite database (not for version control)
```

`PHOENIXMARKETPLACE/` is an older copy of the project kept for reference; the
application does not use it.

## Known limitations

- SQLite with a single gunicorn worker suits a small shop; heavier traffic would
  need a different database and a shared rate-limit store.
