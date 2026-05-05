#!/usr/bin/env python3
"""price_locally.py — Fetch eBay sold-listing prices from your home machine
and push the results to the server's cache.

Run this from the project root whenever you want to refresh prices:

    python price_locally.py

Requires SERVER_URL in your local .env file, e.g.:
    SERVER_URL=https://parts.islandroots.com

Dependencies (install once):
    pip install requests beautifulsoup4 lxml python-dotenv undetected-chromedriver selenium
"""

from __future__ import annotations

import argparse
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
DELAY_SEC = 2.0    # pause between eBay requests
BATCH_SIZE = 50    # how many results to POST at once


# ---------------------------------------------------------------------------
# Chrome browser (shared, warmed-up once)
# ---------------------------------------------------------------------------

_driver = None


def _get_driver():
    global _driver
    if _driver is not None:
        return _driver
    log.info("Launching Chrome …")
    opts = uc.ChromeOptions()
    opts.add_argument("--window-size=1280,800")
    opts.add_argument("--lang=en-US")
    _driver = uc.Chrome(options=opts, headless=False, version_main=147)
    log.info("Warming up eBay session …")
    _driver.get("https://www.ebay.com/")
    time.sleep(3)
    return _driver


def close_browser():
    global _driver
    if _driver:
        _driver.quit()
    _driver = None


def _get_html(url: str) -> str | None:
    """Navigate to url with Chrome, wait for s-item listings, return page source."""
    try:
        driver = _get_driver()
        driver.get(url)
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li.s-item"))
            )
        except Exception:
            log.warning("Timed out waiting for results. Title: %r", driver.title)
            return None
        return driver.page_source
    except Exception as exc:
        log.warning("Browser error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Curated parts pricing
# ---------------------------------------------------------------------------

def fetch_price(query: str) -> tuple[float | None, int, list[float]]:
    """Fetch median sold price for a specific part query."""
    url = (
        "https://www.ebay.com/sch/i.html"
        f"?_nkw={quote_plus(query)}"
        "&_sacat=0&LH_Sold=1&LH_Complete=1&_ipg=60"
    )
    html = _get_html(url)
    if not html:
        return None, 0, []

    soup = BeautifulSoup(html, "lxml")
    prices: list[float] = []
    for item in soup.select("li.s-item"):
        title_tag = item.select_one(".s-item__title")
        if title_tag and "shop on ebay" in title_tag.get_text(strip=True).lower():
            continue
        price_tag = item.select_one(".s-item__price")
        if not price_tag:
            continue
        nums = [
            float(m.group(1).replace(",", ""))
            for m in PRICE_RE.finditer(price_tag.get_text(" ", strip=True))
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
# Top-sold parts search (by vehicle year/make/model)
# ---------------------------------------------------------------------------

def fetch_top_sold(year, make: str, model: str, n: int = 20) -> list[dict]:
    """Search eBay sold listings for a vehicle and return top n by price."""
    query = f"{year} {make} {model}"
    url = (
        "https://www.ebay.com/sch/i.html"
        f"?_nkw={quote_plus(query)}"
        "&_sacat=33637"   # Car & Truck Parts & Accessories
        "&LH_Sold=1&LH_Complete=1"
        "&_sop=16"        # price highest first
        "&_ipg=60"
    )
    html = _get_html(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []

    for item in soup.select("li.s-item"):
        title_tag = item.select_one(".s-item__title")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        if "shop on ebay" in title.lower():
            continue

        price_tag = item.select_one(".s-item__price")
        if not price_tag:
            continue
        nums = [
            float(m.group(1).replace(",", ""))
            for m in PRICE_RE.finditer(price_tag.get_text(" ", strip=True))
        ]
        if not nums:
            continue
        price = sum(nums) / len(nums) if len(nums) > 1 else nums[0]

        link_tag = item.select_one("a.s-item__link")
        item_url = ""
        if link_tag and link_tag.get("href"):
            item_url = link_tag["href"].split("?")[0]

        sold_date = ""
        for sel in (".s-item__caption--signal", ".s-item__endedDate", ".POSITIVE"):
            date_tag = item.select_one(sel)
            if date_tag:
                sold_date = date_tag.get_text(strip=True)
                break

        results.append({
            "title": title,
            "price_usd": price,
            "url": item_url,
            "sold_date_str": sold_date,
        })

        if len(results) >= n * 2:
            break

    results.sort(key=lambda x: x["price_usd"], reverse=True)
    return results[:n]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server",
        default=os.getenv("SERVER_URL", ""),
        help="Server base URL, e.g. https://parts.islandroots.com",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch prices but don't push to server",
    )
    parser.add_argument(
        "--skip-parts",
        action="store_true",
        help="Skip curated parts pricing, only run top-sold",
    )
    parser.add_argument(
        "--skip-top-sold",
        action="store_true",
        help="Skip top-sold search, only run curated parts pricing",
    )
    args = parser.parse_args()

    server = args.server.rstrip("/")
    if not server:
        log.error("SERVER_URL not set. Add it to .env or pass --server https://your-server")
        sys.exit(1)

    log.info("Connecting to %s …", server)

    # -----------------------------------------------------------------------
    # 1. Curated parts pricing
    # -----------------------------------------------------------------------
    if not args.skip_parts:
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
            log.info("Curated parts: all prices are up to date.")
        else:
            log.info("%d curated part queries to price (~%d min)", total, int(total * DELAY_SEC / 60))
            results: list[dict] = []
            for i, query in enumerate(queries, 1):
                log.info("[%d/%d] %s", i, total, query)
                median, n, raw = fetch_price(query)
                if median is not None:
                    log.info("        -> $%.0f  (n=%d)", median, n)
                else:
                    log.info("        -> no results")
                results.append({
                    "query": query,
                    "median_price_usd": median,
                    "sample_size": n,
                    "raw_prices": raw,
                })
                if i < total:
                    time.sleep(DELAY_SEC)

            if not args.dry_run:
                log.info("Uploading curated part prices …")
                total_stored = 0
                for start in range(0, len(results), BATCH_SIZE):
                    batch = results[start: start + BATCH_SIZE]
                    try:
                        r = _requests.post(f"{server}/api/ebay-cache", json=batch, timeout=30)
                        r.raise_for_status()
                        total_stored += r.json().get("stored", len(batch))
                        log.info("  Uploaded %d/%d", total_stored, len(results))
                    except Exception as exc:
                        log.error("Upload failed: %s", exc)
                        sys.exit(1)
                log.info("Curated parts done. %d prices uploaded.", total_stored)
            else:
                for r in results:
                    print(f"  {r['query']}: ${r['median_price_usd']}")

    # -----------------------------------------------------------------------
    # 2. Top-sold search (per vehicle)
    # -----------------------------------------------------------------------
    if not args.skip_top_sold:
        try:
            r = _requests.get(f"{server}/api/pending-top-sold", timeout=30)
            r.raise_for_status()
        except Exception as exc:
            log.error("Could not reach server for top-sold: %s", exc)
            sys.exit(1)

        vehicles = r.json()["vehicles"]
        total_v = r.json()["total"]

        if not vehicles:
            log.info("Top-sold: all vehicles are up to date.")
        else:
            log.info("%d vehicles need top-sold refresh", total_v)
            top_sold_batch: list[dict] = []

            for i, v in enumerate(vehicles, 1):
                label = f"{v['year']} {v['make']} {v['model']}"
                log.info("[%d/%d] Top-sold: %s", i, total_v, label)
                items = fetch_top_sold(v["year"], v["make"], v["model"])
                log.info("        -> %d items, top price $%.0f",
                         len(items), items[0]["price_usd"] if items else 0)
                top_sold_batch.append({
                    "vehicle_id": v["vehicle_id"],
                    "items": items,
                })
                if i < total_v:
                    time.sleep(DELAY_SEC)

            if not args.dry_run and top_sold_batch:
                log.info("Uploading top-sold results …")
                try:
                    r = _requests.post(
                        f"{server}/api/top-sold-cache",
                        json=top_sold_batch,
                        timeout=60,
                    )
                    r.raise_for_status()
                    stored = r.json().get("stored", 0)
                    log.info("Top-sold done. %d items uploaded.", stored)
                except Exception as exc:
                    log.error("Top-sold upload failed: %s", exc)
                    sys.exit(1)

    # -----------------------------------------------------------------------
    # 3. Trigger pipeline run to recompute vehicle values from fresh cache
    # -----------------------------------------------------------------------
    if not args.dry_run:
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

    close_browser()
    log.info("All done.")


if __name__ == "__main__":
    main()
