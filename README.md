# Used Vehicle Parts Search

A daily-running web app that:

1. Searches **Row52** for Volkswagen and BMW vehicles within 50 miles of ZIP **94591**.
2. For each vehicle, looks up **eBay sold listings** for a curated list of high-resale parts.
3. Stores everything in a local SQLite database.
4. Serves a **dashboard** where you can sort/filter vehicles by estimated total parts value.

Built to run anywhere Python runs — locally on your laptop, on a small VPS, or in a container.

---

## Quick start

```bash
# 1. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate            # Windows PowerShell

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) copy env template if you plan to use eBay API later
cp .env.example .env

# 4. Run a one-off search to populate the database
python -m app.run_now

# 5. Start the dashboard
uvicorn app.main:app --reload --port 8000
# Open http://localhost:8000
```

The first `run_now` will take a few minutes — it pulls every page of Row52 results in the radius (~7 pages of 30), filters to VW + BMW, then queries eBay's sold listings for each curated part per vehicle. Subsequent runs are faster because eBay price lookups are cached for 7 days per part-query.

To run automatically once a day, leave the server running — APScheduler is configured inside the FastAPI app and fires at **03:00 local time** by default. You can also trigger a manual refresh from the dashboard's **Run now** button.

---

## How "good resale value" is decided

Every Row52 vehicle is matched against a **curated parts catalog** (`app/parts_catalog.py`) tailored for VW and BMW. Examples:

- **BMW**: adaptive xenon headlights, iDrive/CIC/NBT screens, M-Sport steering wheels, M wheels, DCT/SMG transmissions, N54/N55 turbos, kidney grilles, sport seats, differentials, DME/ECU, catalytic converters.
- **Volkswagen**: DSG transmissions, RNS-510/315 head units, K03/K04 turbos, R32/GTI wheels, sport steering wheels, Xenon headlights, ECUs, catalytic converters.

For each `(year, model, part)` tuple, the eBay scraper queries **completed + sold** listings, takes the **median** of up to 20 results, and stores it as `estimated_resale_usd`. The dashboard shows the **sum** of medians as the vehicle's `estimated_total_value`.

Edit `app/parts_catalog.py` to add or remove parts. Each entry has a search-template string with `{year}` and `{model}` placeholders.

---

## Project layout

```
app/
├── main.py            FastAPI app, scheduler hook, static-file mount
├── config.py          Constants: ZIP, radius, makes, eBay API toggle
├── database.py        SQLAlchemy engine + session
├── models.py          ORM tables: Vehicle, PartEstimate, SearchRun
├── parts_catalog.py   Curated VW/BMW high-value-parts list
├── pipeline.py        Orchestrator: scrape Row52 → eBay → DB
├── scheduler.py       APScheduler daily job
├── run_now.py         CLI: trigger one full pipeline run
├── scrapers/
│   ├── row52.py       Polite Row52 scraper (paginated, schema.org-based)
│   └── ebay.py        eBay sold-listings scraper, with API plug-in point
└── static/
    └── index.html     Dashboard (vanilla JS + Tailwind CDN)
data/
└── app.db             SQLite database (auto-created on first run)
```

---

## Plugging in the official eBay API later

When you register at https://developer.ebay.com and get an OAuth client ID + secret, drop them into `.env`:

```
EBAY_USE_API=1
EBAY_CLIENT_ID=...
EBAY_CLIENT_SECRET=...
```

The eBay scraper checks `EBAY_USE_API` and switches to the official **Browse API** path (already stubbed in `app/scrapers/ebay.py::fetch_sold_via_api`). No other code changes required.

---

## Configuration

Tunables live in `app/config.py`:

| Constant            | Default        | Meaning                                       |
|---------------------|----------------|-----------------------------------------------|
| `ZIP_CODE`          | `"94591"`      | Search center                                 |
| `RADIUS_MILES`      | `50`           | Row52 distance filter                         |
| `TARGET_MAKES`      | `["BMW","Volkswagen"]` | Post-filter (case-insensitive)        |
| `MIN_VEHICLE_VALUE` | `300`          | Hide vehicles below this estimated total     |
| `EBAY_RESULTS_PER_QUERY` | `20`      | How many sold listings to median over         |
| `EBAY_CACHE_DAYS`   | `7`            | Cache TTL for eBay price lookups              |
| `DAILY_RUN_HOUR`    | `3`            | Local-time hour for daily auto-run            |

---

## Deploying

- **DigitalOcean Droplet** — run `sudo bash deploy/deploy.sh` for first-time setup, then `sudo bash deploy/update.sh` for subsequent code updates. The scripts handle virtualenv, supervisor, and Nginx automatically.
- **Railway / Render / Fly.io** — push the repo and point the start command at `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. SQLite is fine for a single instance; switch `DB_URL` to Postgres in `app/config.py` if you scale beyond one worker.
- **Plain VPS** — same as the Droplet setup above.

---

## Important: terms of service

Both **Row52** and **eBay** restrict automated access in their Terms of Service. This app is intended for **personal research / personal use** at low volume, and ships with:

- A 2-second delay between Row52 page fetches.
- A 4-second delay between eBay sold-listings queries (and a 7-day cache to minimize repeats).
- A descriptive User-Agent string identifying the request.
- No login automation, no bypassing of paywalls, no captcha-solving.

You should review each site's terms and decide whether your usage is compatible. For commercial or higher-volume use, you should switch to:

- **eBay's Browse API** (free, registration required) — already stubbed.
- A direct **data-licensing arrangement with Row52** if available — there is no public Row52 API.

This README and the code are not legal advice. Use responsibly.
