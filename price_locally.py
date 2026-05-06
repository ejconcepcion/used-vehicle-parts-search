#!/usr/bin/env python3
"""price_locally.py — Fetch the top 30 highest-value recently sold eBay parts
for each vehicle in the yard and push results to the server.

Run from the project root:

    python price_locally.py

Requires SERVER_URL in your local .env file:
    SERVER_URL=https://parts.islandroots.com

Dependencies (install once):
    pip install requests beautifulsoup4 lxml python-dotenv undetected-chromedriver selenium
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from collections import defaultdict
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

PRICE_RE  = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
DELAY_SEC = 2.0   # polite pause between vehicles
TOP_N     = 30    # unique parts to return per vehicle


# ---------------------------------------------------------------------------
# Chrome browser — opened once, reused for all searches
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
    """Load a URL in Chrome, wait for listings, return page source."""
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
# Deduplication helpers
# ---------------------------------------------------------------------------

_NOISE_RE = re.compile(
    r"\b(?:oem|genuine|original|factory|aftermarket|fits?|for|new|used|"
    r"tested|good|working|excellent|clean|nice|rare|assy|assembly|complete|"
    r"set|pair|kit|unit|module|w|with|without|and|the|a|an|no|"
    r"lh|rh|fl|fr|rl|rr|left|right|front|rear|upper|lower|inner|outer|"
    r"driver|passenger|side|part|parts|number|oem#)\b",
    re.IGNORECASE,
)


def _part_key(title: str, year, make: str, model: str) -> str:
    """Normalize a listing title to a short dedup key."""
    t = title.lower()
    for word in [str(year)] + make.lower().split() + model.lower().split():
        t = re.sub(r"\b" + re.escape(word) + r"\b", " ", t)
    t = _NOISE_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    tokens = t.split()[:3]
    return " ".join(tokens) if tokens else title[:20].lower()


def _deduplicate(raw: list[dict], year, make: str, model: str) -> list[dict]:
    """Group listings by normalized part key, average prices, return sorted list."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in raw:
        key = _part_key(item["title"], year, make, model)
        groups[key].append(item)

    deduped = []
    for group in groups.values():
        avg_price = sum(i["price_usd"] for i in group) / len(group)
        rep = max(group, key=lambda x: x["price_usd"])
        deduped.append({
            "title": rep["title"],
            "price_usd": round(avg_price, 2),
            "url": rep["url"],
            "sold_date_str": rep["sold_date_str"],
            "sample_count": len(group),
        })

    deduped.sort(key=lambda x: x["price_usd"], reverse=True)
    return deduped


# ---------------------------------------------------------------------------
# eBay top-sold fetch
# ---------------------------------------------------------------------------

def fetch_top_sold(year, make: str, model: str, n: int = TOP_N) -> list[dict]:
    """Search eBay sold listings for a vehicle and return top n unique parts by avg price."""
    query = f"{year} {make} {model}"
    # Fetch several pages so deduplication still leaves us with n unique parts
    raw: list[dict] = []
    for page in range(1, 5):
        url = (
            "https://www.ebay.com/sch/i.html"
            f"?_nkw={quote_plus(query)}"
            "&_sacat=33637"       # Car & Truck Parts & Accessories
            "&LH_Sold=1&LH_Complete=1"
            "&_sop=16"            # price highest first
            f"&_ipg=60&_pgn={page}"
        )
        html = _get_html(url)
        if not html:
            break

        soup = BeautifulSoup(html, "lxml")
        page_count = 0
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

            raw.append({"title": title, "price_usd": price,
                         "url": item_url, "sold_date_str": sold_date})
            page_count += 1

        if page_count < 10:
            break   # reached the last page
        if page < 4:
            time.sleep(1)

    deduped = _deduplicate(raw, year, make, model)
    return deduped[:n]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("SERVER_URL", ""),
                        help="Server base URL, e.g. https://parts.islandroots.com")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch data but don't push to server")
    args = parser.parse_args()

    server = args.server.rstrip("/")
    if not server:
        log.error("SERVER_URL not set. Add it to .env or pass --server https://your-server")
        sys.exit(1)

    log.info("Connecting to %s …", server)

    # Fetch vehicles that need a top-sold refresh (missing or older than 24 h)
    try:
        r = _requests.get(f"{server}/api/pending-top-sold", timeout=30)
        r.raise_for_status()
    except Exception as exc:
        log.error("Could not reach server: %s", exc)
        sys.exit(1)

    vehicles = r.json()["vehicles"]
    total    = r.json()["total"]

    if not vehicles:
        log.info("All vehicles are up to date — nothing to do.")
        return

    log.info("%d vehicle(s) need a parts refresh", total)
    batch: list[dict] = []

    for i, v in enumerate(vehicles, 1):
        label = f"{v['year']} {v['make']} {v['model']}"
        log.info("[%d/%d] %s", i, total, label)
        items = fetch_top_sold(v["year"], v["make"], v["model"])
        if items:
            log.info("  -> %d unique parts | top: %s — $%.0f (avg of %d)",
                     len(items), items[0]["title"][:55],
                     items[0]["price_usd"], items[0]["sample_count"])
        else:
            log.info("  -> no results")

        batch.append({"vehicle_id": v["vehicle_id"], "items": items})

        if i < total:
            time.sleep(DELAY_SEC)

    if args.dry_run:
        log.info("Dry run — skipping upload.")
        close_browser()
        return

    log.info("Uploading results …")
    try:
        r = _requests.post(f"{server}/api/top-sold-cache", json=batch, timeout=60)
        r.raise_for_status()
        log.info("Uploaded %d items.", r.json().get("stored", 0))
    except Exception as exc:
        log.error("Upload failed: %s", exc)
        close_browser()
        sys.exit(1)

    # Trigger pipeline so Row52 data stays current
    try:
        r = _requests.post(f"{server}/api/run-now", timeout=30)
        if r.status_code == 202:
            log.info("Pipeline started — check the dashboard in a few minutes.")
        elif r.status_code == 409:
            log.info("Pipeline already running.")
    except Exception as exc:
        log.warning("Could not trigger pipeline: %s", exc)

    close_browser()
    log.info("Done.")


if __name__ == "__main__":
    main()
