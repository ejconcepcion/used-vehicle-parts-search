#!/usr/bin/env python3
"""price_locally.py — Fetch the top 30 highest-value recently sold eBay parts
for each vehicle in the yard and push results to the server.

Run from the project root:

    python price_locally.py

Requires SERVER_URL in your local .env file:
    SERVER_URL=https://parts.islandroots.com

Dependencies (install once):
    pip install requests beautifulsoup4 lxml python-dotenv
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

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s :: %(message)s",
)
log = logging.getLogger(__name__)

PRICE_RE  = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
DELAY_SEC = 3.0   # polite pause between eBay requests
TOP_N     = 30    # unique parts to return per vehicle
DEBUG     = False  # set True via --debug

# Realistic browser headers
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Shared session keeps cookies across requests (important for eBay)
_session = requests.Session()
_session.headers.update(HEADERS)
_ebay_warmed = False


def _warm_up():
    """Visit eBay homepage once to get session cookies before searching."""
    global _ebay_warmed
    if _ebay_warmed:
        return
    try:
        log.info("Warming up eBay session …")
        _session.get("https://www.ebay.com/", timeout=20)
        time.sleep(2)
        _ebay_warmed = True
    except Exception as exc:
        log.warning("Warm-up failed (continuing anyway): %s", exc)
        _ebay_warmed = True


def _get_html(url: str, label: str = "") -> str | None:
    """Fetch a URL and return HTML. Logs title and item count for debugging."""
    _warm_up()
    try:
        resp = _session.get(url, timeout=30)
        if not resp.ok:
            log.warning("eBay returned HTTP %d for %s", resp.status_code, label or url[:80])
            return None
        html = resp.text

        soup = BeautifulSoup(html, "lxml")
        title = soup.title.string.strip() if soup.title else "(no title)"
        s_items = len(soup.select("li.s-item"))
        s_cards = len(soup.select(".s-card"))
        log.info("  [page] %r  li.s-item=%d  .s-card=%d", title[:80], s_items, s_cards)

        if DEBUG:
            with open("debug_ebay.html", "w", encoding="utf-8") as f:
                f.write(html)
            log.info("  [debug] saved debug_ebay.html")

        return html
    except Exception as exc:
        log.warning("Request error for %s: %s", label or url[:80], exc)
        return None


def _parse_items(html: str) -> list[dict]:
    """Parse eBay search results — handles both s-item and s-card layouts."""
    soup = BeautifulSoup(html, "lxml")
    results = []

    # --- Modern s-card layout ---
    for card in soup.select(".s-card"):
        title_tag = card.select_one(".s-card__title, .s-card__title--has-price")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        if "shop on ebay" in title.lower():
            continue

        price_tag = card.select_one(".s-card__price")
        if not price_tag:
            continue
        nums = [float(m.group(1).replace(",", ""))
                for m in PRICE_RE.finditer(price_tag.get_text(" ", strip=True))]
        if not nums:
            continue
        price = sum(nums) / len(nums) if len(nums) > 1 else nums[0]

        link_tag = card.select_one("a.s-card__link, a[href*='/itm/']")
        item_url = ""
        if link_tag and link_tag.get("href"):
            item_url = link_tag["href"].split("?")[0]

        sold_date = ""
        for sel in (".s-card__subtitle", ".s-card__attribute"):
            date_tag = card.select_one(sel)
            if date_tag:
                sold_date = date_tag.get_text(strip=True)
                break

        results.append({"title": title, "price_usd": price,
                         "url": item_url, "sold_date_str": sold_date})

    # --- Classic s-item layout (fallback) ---
    if not results:
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
            nums = [float(m.group(1).replace(",", ""))
                    for m in PRICE_RE.finditer(price_tag.get_text(" ", strip=True))]
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

            results.append({"title": title, "price_usd": price,
                             "url": item_url, "sold_date_str": sold_date})

    return results


# ---------------------------------------------------------------------------
# Deduplication
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
    t = title.lower()
    for word in [str(year)] + make.lower().split() + model.lower().split():
        t = re.sub(r"\b" + re.escape(word) + r"\b", " ", t)
    t = _NOISE_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    tokens = t.split()[:3]
    return " ".join(tokens) if tokens else title[:20].lower()


def _deduplicate(raw: list[dict], year, make: str, model: str) -> list[dict]:
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
    """Search eBay sold listings for a vehicle, deduplicate, return top n by avg price."""
    query = f"{year} {make} {model}"
    raw: list[dict] = []

    for page in range(1, 5):
        url = (
            "https://www.ebay.com/sch/i.html"
            f"?_nkw={quote_plus(query)}"
            "&_sacat=0"
            "&LH_Sold=1&LH_Complete=1"
            "&rt=nc"
            f"&_ipg=60&_pgn={page}"
        )
        html = _get_html(url, label=f"{query} p{page}")
        if not html:
            break

        page_items = _parse_items(html)
        log.debug("  page %d: %d items parsed", page, len(page_items))
        raw.extend(page_items)

        if len(page_items) < 10:
            break  # last page
        if page < 4:
            time.sleep(1.5)

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
    parser.add_argument("--limit", type=int, default=0,
                        help="Only process the first N vehicles (default: all)")
    parser.add_argument("--debug", action="store_true",
                        help="Save the first eBay page HTML to debug_ebay.html")
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    server = args.server.rstrip("/")
    if not server:
        log.error("SERVER_URL not set. Add it to .env or pass --server https://your-server")
        sys.exit(1)

    log.info("Connecting to %s …", server)

    try:
        r = requests.get(f"{server}/api/pending-top-sold", timeout=30)
        r.raise_for_status()
    except Exception as exc:
        log.error("Could not reach server: %s", exc)
        sys.exit(1)

    vehicles = r.json()["vehicles"]
    total    = r.json()["total"]

    if args.limit:
        vehicles = vehicles[:args.limit]
        total = len(vehicles)
        log.info("(limited to first %d vehicle(s))", total)

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
        return

    log.info("Uploading results …")
    try:
        r = requests.post(f"{server}/api/top-sold-cache", json=batch, timeout=60)
        r.raise_for_status()
        log.info("Uploaded %d items.", r.json().get("stored", 0))
    except Exception as exc:
        log.error("Upload failed: %s", exc)
        sys.exit(1)

    try:
        r = requests.post(f"{server}/api/run-now", timeout=30)
        if r.status_code == 202:
            log.info("Pipeline started — check the dashboard in a few minutes.")
        elif r.status_code == 409:
            log.info("Pipeline already running.")
    except Exception as exc:
        log.warning("Could not trigger pipeline: %s", exc)

    log.info("Done.")


if __name__ == "__main__":
    main()
