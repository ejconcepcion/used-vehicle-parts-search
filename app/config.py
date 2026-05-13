"""Centralized configuration. Edit values here to retune the search."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Paths -----------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "app.db"
DB_URL = f"sqlite:///{DB_PATH}"

# --- Row52 search ----------------------------------------------------------

ZIP_CODE = "94591"
RADIUS_MILES = 60
TARGET_MAKES = ["BMW", "Volkswagen", "Mercedes-Benz", "Audi"]   # post-filter, case-insensitive
ROW52_PAGE_DELAY_SEC = 2.0             # be polite

# Yards that Row52's ZIP+radius search misses due to a site bug (newly added
# locations with high locationIds aren't indexed in their geo search).
# Each entry is searched directly by locationId and merged/deduplicated by VIN.
# 10798 = PICK-n-PULL American Canyon (5 mi from 94591, closest yard)
EXTRA_LOCATION_IDS: list[int] = [10798]

# --- eBay ------------------------------------------------------------------

EBAY_USE_API = os.getenv("EBAY_USE_API", "0") == "1"
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")
EBAY_API_BASE = os.getenv("EBAY_API_BASE", "https://api.ebay.com")
EBAY_QUERY_DELAY_SEC = 4.0
EBAY_RESULTS_PER_QUERY = 20
EBAY_CACHE_DAYS = 7

# Optional proxy for eBay requests (required when running from a datacenter IP).
# Supports HTTP, HTTPS, and SOCKS5 proxies.
# Examples:
#   EBAY_PROXY=http://user:pass@host:port
#   EBAY_PROXY=socks5://user:pass@host:port
#   EBAY_PROXY=http://host:port   (no auth)
EBAY_PROXY = os.getenv("EBAY_PROXY", "")

# --- Server-side pricing ---------------------------------------------------
# Set SERVER_SIDE_PRICING=1 in .env to fetch Terapeak data automatically
# after each Row52 scrape. Requires EBAY_TERAPEAK_COOKIE to be set.
# Leave at 0 to use price_locally.py instead (safe rollback path).

SERVER_SIDE_PRICING = os.getenv("SERVER_SIDE_PRICING", "0") == "1"
EBAY_TERAPEAK_COOKIE = os.getenv("EBAY_TERAPEAK_COOKIE", "")

# --- Pipeline tuning -------------------------------------------------------

MIN_VEHICLE_VALUE = 300        # hide vehicles below this in the dashboard
PARTS_PER_VEHICLE_LIMIT = 20   # cap parts queried per vehicle (cost control)

# --- Scheduler -------------------------------------------------------------

DAILY_RUN_HOUR = 3   # 0-23, UTC (the scheduler runs in UTC)
DAILY_RUN_MINUTE = 0


# --- HTTP ------------------------------------------------------------------

USER_AGENT = os.getenv(
    "USER_AGENT",
    "UsedVehiclePartsSearch/0.1 (+personal-research; contact via local instance)",
)
