# Used Vehicle Parts Search — Codebase Guide

## What this is

A personal-use web app that scrapes **Row52** for BMW/VW vehicles at pick-and-pull yards
within 60 miles of ZIP 94591 (Vallejo, CA), then cross-references eBay sold-listing data to
estimate how much the parts are worth. A FastAPI + SQLite server handles storage and the
dashboard; a separate local script (`price_locally.py`) handles eBay pricing because eBay
blocks datacenter IPs.

---

## Architecture

```
Row52 scraper  ──►  SQLite DB (data/app.db)  ──►  Dashboard (/api/vehicles)
                          ▲
price_locally.py  ────────┘
 (runs on local machine with residential IP; pushes results to /api/top-sold-cache)
```

### Server side (`uvicorn app.main:app`)
- Scrapes Row52 daily via APScheduler (03:00 UTC, configurable in `app/config.py`)
- Stores vehicles in `vehicle` table; upserts on VIN
- Serves REST API + vanilla-JS dashboard (`app/static/index.html`)

### Local-machine side (`python price_locally.py`)
- Calls `GET /api/pending-top-sold` to get which vehicles need pricing
- Scrapes eBay sold listings (or queries Terapeak if `EBAY_TERAPEAK_COOKIE` is set)
- Posts results to `POST /api/top-sold-cache`
- Triggers `POST /api/run-now` so the dashboard refreshes

---

## Key files

| File | Role |
|------|------|
| `app/config.py` | All tunables (ZIP, radius, makes, delays, schedule time) |
| `app/models.py` | SQLAlchemy ORM: `Vehicle`, `PartEstimate`, `EbayPriceCache`, `TopSoldPart`, `SearchRun` |
| `app/database.py` | Engine setup (WAL mode), forward migrations, `session_scope()` context manager |
| `app/pipeline.py` | Scrape → filter → upsert orchestrator; called by scheduler and `run_now` |
| `app/scrapers/row52.py` | Row52 pagination scraper; filters by make, year ≥ 2005, added within 7 days |
| `app/scrapers/ebay.py` | eBay sold-listing HTML scraper + Browse API stub |
| `app/parts_catalog.py` | Curated `CatalogPart` list per make (BMW, VW); `parts_for_vehicle()` applies model filter |
| `app/main.py` | FastAPI app: all API endpoints + static file mount |
| `app/scheduler.py` | APScheduler background job with run-lock to prevent overlapping runs |
| `app/progress.py` | Thread-safe in-memory progress state for the live progress bar |
| `price_locally.py` | Standalone local script for eBay/Terapeak pricing and server upload |

---

## Database schema

```
vehicle            — one row per VIN; estimated_total_value is net (after fees/shipping)
part_estimate      — one row per (vehicle, part_name); populated by the curated catalog pipeline
ebay_price_cache   — keyed by query string; 7-day TTL; reused across similar vehicles
top_sold_part      — top 30 recently sold eBay listings per vehicle; populated by price_locally.py
search_run         — audit log; one row per pipeline invocation
```

Forward-only migrations run at startup via `database._migrate_db()`. Adding a column means
adding an `if col_name not in cols` block there.

---

## Environment variables (`.env`)

| Variable | Required | Purpose |
|----------|----------|---------|
| `SERVER_URL` | Local only | Base URL the local pricer pushes results to |
| `EBAY_TERAPEAK_COOKIE` | Optional | Seller Hub cookie; enables Terapeak path (more reliable than scraping) |
| `EBAY_USE_API` | Optional | Set `1` to use Browse API stub (active listings only — not truly sold data) |
| `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` | With `EBAY_USE_API=1` | eBay OAuth credentials |
| `EBAY_PROXY` | Optional | HTTP/SOCKS5 proxy for eBay requests from datacenter IPs |
| `USER_AGENT` | Optional | Overrides the default identifying User-Agent string |

---

## Supplemental yard workaround

Row52's ZIP+radius geo-search silently omits newly added yards (high `locationId` values aren't
indexed). The American Canyon Pick-n-Pull (locationId 10798, ~5 miles from 94591) is in this
category. `config.EXTRA_LOCATION_IDS` lists yards to fetch directly by ID; `row52.search()`
does a second pass for each and deduplicates by VIN.

---

## eBay pricing flow

1. Dashboard shows "No data yet" until `price_locally.py` has run.
2. `GET /api/pending-top-sold` returns vehicles with no `top_sold_part` rows or data older than 24 h.
3. Local script fetches eBay sold listings (Terapeak if cookie is set, HTML scrape otherwise).
4. Results are deduplicated by part-type key (`_part_key`) and averaged across identical-title groups.
5. Batch is saved to `last_batch.json` then posted to `/api/top-sold-cache`.
6. Server replaces `TopSoldPart` rows for each vehicle and updates `estimated_total_value`.

Net value formula (applied in pipeline, not the local script):
`net = gross_median × 0.87 − 0.30 − shipping_est`
(accounts for ~13% eBay + payment fees and per-item $0.30 fee)

---

## Running locally

```powershell
# One-time setup
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Populate DB (scrapes Row52 only, no eBay pricing yet)
python -m app.run_now

# Start server + dashboard
uvicorn app.main:app --reload --port 8000

# Fetch eBay pricing and push to local server
$env:SERVER_URL = "http://localhost:8000"
python price_locally.py
```

---

## Known constraints / watch-outs

- **`date_added_to_yard` is a string** (`"Apr 28, 2026"`, `%b %d, %Y`). The 7-day recency
  filter in `/api/vehicles` is applied in Python after the SQL `LIMIT`, so if the DB has many
  stale rows the effective result count may be less than the configured limit. The scraper
  already enforces the 7-day filter at scrape time, so this only affects pre-existing DB rows.

- **`_parse_yard_date` is duplicated** in `app/main.py` and `app/scrapers/row52.py` — they
  must stay in sync.

- **eBay Browse API stub** (`fetch_sold_via_api`) returns *active* listings only; the eBay
  Finding API for true sold history is deprecated. Terapeak is the best authenticated path.

- **`vehicles_matched` == `vehicles_seen`** in `pipeline.py` — filtering happens inside
  `row52.search()`, so only matched vehicles are returned; both counts are always equal.

- **Deploy scripts** referenced in README (`deploy/deploy.sh`, `deploy/update.sh`) are not
  in the repo — `deploy/nginx.conf` and `deploy/crontab-entry.txt` are reference templates only.

- **No authentication** on any API endpoint. The `/api/clear-cache` and `/api/run-now` POST
  endpoints are unprotected. Fine for local use; add HTTP Basic Auth via Nginx if deployed
  publicly.

- **XSS in dashboard**: vehicle data (yard name, model, image URL) is injected directly into
  `innerHTML` without sanitization. Scraped content is the only input source; no user-supplied
  data flows into the DOM.
