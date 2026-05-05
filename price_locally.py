#!/usr/bin/env python3
"""price_locally.py — Fetch eBay sold-listing prices from your home machine
and push the results to the server's cache.

Run this from the project root whenever you want to refresh prices:

    python price_locally.py

Requires SERVER_URL in your local .env file, e.g.:
    SERVER_URL=http://your-server-ip

Dependencies (install once):
    pip install requests beautifulsoup4 lxml python-dotenv curl_cffi
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
import sys
import time
from urllib.parse import quote_plus

import requests as _requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s :: %(message)s",
)
log = logging.getLogger(__name__)

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
DELAY_SEC = 2.0          # pause between eBay requests (be polite)
BATCH_SIZE = 50          # how many results to POST at once


# ---------------------------------------------------------------------------
# eBay scraper — uses real Chrome to pass JS challenges
# ---------------------------------------------------------------------------

_driver = None


def _get_driver():
    """Return a warmed-up Chrome WebDriver."""
    global _driver
    if _driver is not None:
        return _driver

    log.info("Launching Chrome …")
    opts = uc.ChromeOptions()
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--lang=en-US")
    _driver = uc.Chrome(options=opts, headless=False, version_main=147)

    # Warm up — visit homepage to get cookies.
    log.info("Warming up eBay session …")
    _driver.get("https://www.ebay.com/")
    time.sleep(3)
    return _driver


def close_browser():
    global _driver
    if _driver:
        _driver.quit()
    _driver = None


def fetch_price(query: str) -> tuple[float | None, int, list[float]]:
    url = (
        "https://www.ebay.com/sch/i.html"
        f"?_nkw={quote_plus(query)}"
        "&_sacat=0&LH_Sold=1&LH_Complete=1&_ipg=60"
    )
    try:
        driver = _get_driver()
        driver.get(url)
        # Wait up to 8s for listings to appear.
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".s-card__price"))
            )
        except Exception:
            log.warning("Timed out waiting for results. Page title: %r", driver.title)
            return None, 0, []
        html = driver.page_source
    except Exception as exc:
        log.warning("Browser error for %r: %s", query, exc)
        return None, 0, []

    soup = BeautifulSoup(html, "lxml")
    prices: list[float] = []
    for tag in soup.select(".s-card__price"):
        # Skip strikethrough (original/crossed-out) prices
        if "strikethrough" in (tag.get("class") or []):
            continue
        nums = [
            float(m.group(1).replace(",", ""))
            for m in PRICE_RE.finditer(tag.get_text(strip=True))
        ]
        if not nums:
            continue
        prices.append(sum(nums) / len(nums) if len(nums) > 1 else nums[0])
        if len(prices) >= 20:
            break

    if not prices:
        return None, 0, []
    return float(statistics.median(prices)), len(prices), prices


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default=os.getenv("SERVER_URL", ""),
        help="Server base URL, e.g. http://123.45.67.89 (or set SERVER_URL in .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch eBay prices but don't push to server",
    )
    args = parser.parse_args()

    server = args.server.rstrip("/")
    if not server:
        log.error("SERVER_URL not set. Add it to .env or pass --server http://your-server")
        sys.exit(1)

    # 1. Fetch pending queries from server
    log.info("Connecting to %s …", server)
    try:
        r = _requests.get(f"{server}/api/pending-queries", timeout=30)
        r.raise_for_status()
    except Exception as exc:
        log.error("Could not reach server: %s", exc)
        sys.exit(1)

    data = r.json()
    queries: list[str] = data["queries"]
    total = data["total"]

    if not queries:
        log.info("All prices are up to date — nothing to do.")
        return

    log.info("%d queries to price (this will take ~%d minutes)", total, int(total * DELAY_SEC / 60))

    # 2. Price each query
    results: list[dict] = []
    for i, query in enumerate(queries, 1):
        log.info("[%d/%d] %s", i, total, query)
        median, n, raw = fetch_price(query)
        if median is not None:
            log.info("        → $%.0f  (n=%d)", median, n)
        else:
            log.info("        → no results")
        results.append({
            "query": query,
            "median_price_usd": median,
            "sample_size": n,
            "raw_prices": raw,
        })
        if i < total:
            time.sleep(DELAY_SEC)

    if args.dry_run:
        log.info("Dry run — skipping upload. Results:")
        for r in results:
            print(f"  {r['query']}: ${r['median_price_usd']}")
        return

    # 3. Push results to server in batches
    log.info("Uploading results to server …")
    total_stored = 0
    for start in range(0, len(results), BATCH_SIZE):
        batch = results[start: start + BATCH_SIZE]
        try:
            r = _requests.post(f"{server}/api/ebay-cache", json=batch, timeout=30)
            r.raise_for_status()
            stored = r.json().get("stored", len(batch))
            total_stored += stored
            log.info("  Uploaded %d/%d", total_stored, len(results))
        except Exception as exc:
            log.error("Upload failed: %s", exc)
            sys.exit(1)

    # 4. Trigger a pipeline run so vehicle values are recomputed from fresh cache
    log.info("Triggering pipeline run to recompute vehicle values …")
    try:
        r = _requests.post(f"{server}/api/run-now", timeout=30)
        if r.status_code == 202:
            log.info("Pipeline started. Check the dashboard in a few minutes.")
        elif r.status_code == 409:
            log.info("Pipeline already running — values will update when it finishes.")
        else:
            log.warning("Unexpected response from run-now: %d", r.status_code)
    except Exception as exc:
        log.warning("Could not trigger pipeline: %s", exc)

    log.info("Done. %d prices uploaded.", total_stored)
    close_browser()


if __name__ == "__main__":
    main()
