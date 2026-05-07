#!/usr/bin/env python3
"""price_locally.py — Fetch the top 30 highest-value recently sold eBay parts
for each vehicle in the yard and push results to the server.

Run from the project root:

    python price_locally.py

Requires SERVER_URL in your local .env file:
    SERVER_URL=https://parts.islandroots.com

Dependencies (install once):
    pip install curl-cffi beautifulsoup4 lxml python-dotenv requests
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from urllib.parse import quote_plus, urlencode

import requests                          # used only for the server upload calls
from curl_cffi import requests as ebay_requests  # Chrome TLS impersonation for eBay
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s :: %(message)s",
)
log = logging.getLogger(__name__)

PRICE_RE       = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
EBAY_JUNK      = re.compile(r"\bopens in a new (window or tab|tab or window)\b", re.IGNORECASE)
NEW_PART_RE    = re.compile(r"\b(brand\s+new|new\s+in\s+box|new\s+old\s+stock|nos)\b", re.IGNORECASE)
DELAY_SEC      = 10.0  # polite pause between vehicles when scraping (eBay rate-limits quickly)
TERAPEAK_DELAY = 1.5   # authenticated API tolerates a much shorter pause
TOP_N          = 50    # unique parts to return per vehicle
DEBUG          = False  # set True via --debug


def _is_new_part(title: str) -> bool:
    """Return True if the title clearly indicates a new (not used) part."""
    return bool(NEW_PART_RE.search(title))

# Realistic browser headers matching a Chrome 124 navigation request
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
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

# curl_cffi session — impersonates Chrome 124 at the TLS level so eBay's
# Akamai bot detection sees a real browser fingerprint, not a Python script.
_session = ebay_requests.Session(impersonate="chrome124")
_session.headers.update(HEADERS)
_ebay_warmed = False


def _warm_up():
    """Visit eBay homepage then a generic search to build up session cookies."""
    global _ebay_warmed
    if _ebay_warmed:
        return
    try:
        log.info("Warming up eBay session …")
        # Step 1: land on homepage (Sec-Fetch-Site: none for first nav)
        _session.headers.update({"Sec-Fetch-Site": "none"})
        _session.get("https://www.ebay.com/", timeout=20)
        time.sleep(2)
        # Step 2: do a plain search (establishes search cookies)
        _session.headers.update({"Sec-Fetch-Site": "same-origin",
                                  "Referer": "https://www.ebay.com/"})
        _session.get("https://www.ebay.com/sch/i.html?_nkw=auto+parts&_sacat=0",
                     timeout=20)
        time.sleep(2)
        _ebay_warmed = True
    except Exception as exc:
        log.warning("Warm-up failed (continuing anyway): %s", exc)
        _ebay_warmed = True


def _get_html(url: str, label: str = "", _retry: bool = True) -> str | None:
    """Fetch a URL and return HTML. Retries once after a long pause if CAPTCHA hit."""
    _warm_up()
    _session.headers.update({"Referer": "https://www.ebay.com/sch/i.html"})
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

        # CAPTCHA / rate-limit page — wait and retry once
        if "pardon our interruption" in title.lower():
            if _retry:
                log.warning("  CAPTCHA hit — waiting 45 s then retrying …")
                time.sleep(45)
                return _get_html(url, label=label, _retry=False)
            log.warning("  CAPTCHA hit again — skipping page")
            return None

        # If eBay ignored the sold filter and returned a "for sale" page, discard it
        if "for sale" in title.lower() and "sold" not in title.lower():
            log.warning("  Sold filter bypassed (got 'for sale' page) — skipping")
            return None

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

    def _clean(t: str) -> str:
        return EBAY_JUNK.sub("", t).strip(" -–|")

    # --- Modern s-card layout ---
    for card in soup.select(".s-card"):
        title_tag = card.select_one(".s-card__title, .s-card__title--has-price")
        if not title_tag:
            continue
        title = _clean(title_tag.get_text(strip=True))
        if "shop on ebay" in title.lower() or _is_new_part(title):
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
            title = _clean(title_tag.get_text(strip=True))
            if "shop on ebay" in title.lower() or _is_new_part(title):
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
# Terapeak (Seller Hub Research) — authenticated JSON endpoint
# ---------------------------------------------------------------------------

def _terapeak_text(node) -> str:
    """Extract concatenated plain text from a TextualDisplay-like node."""
    if not isinstance(node, dict):
        return str(node) if node else ""
    spans = node.get("textSpans") or []
    return "".join(s.get("text", "") for s in spans).strip()


def _terapeak_parse_price(s: str):
    if not s:
        return None
    m = PRICE_RE.search(s)
    return float(m.group(1).replace(",", "")) if m else None


def _terapeak_fetch_page(keywords: str, offset: int, limit: int,
                          cookie: str, now_ms: int, start_ms: int) -> list[dict]:
    """Fetch one page of Terapeak SOLD listings. Returns the raw `results` list."""
    params = [
        ("marketplace", "EBAY-US"),
        ("keywords",    keywords),
        ("dayRange",    "90"),
        ("endDate",     str(now_ms)),
        ("startDate",   str(start_ms)),
        ("categoryId",  "6028"),       # Auto Parts & Accessories
        ("conditionId", "3000"),       # Used
        ("offset",      str(offset)),
        ("limit",       str(limit)),
        ("sorting",     "-avgsalesprice"),
        ("tabName",     "SOLD"),
        ("tz",          "America/Los_Angeles"),
        ("modules",     "aggregates"),
        ("modules",     "searchResults"),
        ("modules",     "resultsHeader"),
    ]
    url = "https://www.ebay.com/sh/research/api/search?" + urlencode(params)
    headers = {
        "accept":           "*/*",
        "accept-language":  "en-US,en;q=0.9",
        "referer":          "https://www.ebay.com/sh/research?tabName=SOLD",
        "user-agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/147.0.0.0 Safari/537.36",
        "x-requested-with": "XMLHttpRequest",
        "cookie":           cookie,
    }

    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except Exception as exc:
        log.warning("  Terapeak request error for %r [off=%d]: %s",
                    keywords, offset, exc)
        return []

    if not resp.ok:
        log.warning("  Terapeak HTTP %d for %r [off=%d]",
                    resp.status_code, keywords, offset)
        return []

    # NDJSON: one module per line
    modules = []
    for ln in resp.text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            modules.append(json.loads(ln))
        except json.JSONDecodeError:
            continue

    search = next((m for m in modules
                   if m.get("_type") == "SearchResultsModule"), None)
    if not search:
        log.warning("  Terapeak: no SearchResultsModule for %r [off=%d] — cookie may be stale",
                    keywords, offset)
        return []
    return search.get("results", []) or []


def fetch_top_sold_terapeak(year, make: str, model: str,
                             n: int = TOP_N,
                             cookie: str | None = None,
                             total_listings: int = 100) -> list[dict]:
    """Use the logged-in Seller Hub Research (Terapeak) JSON endpoint to get
    sold-listing data. Pulls up to `total_listings` rows across pages, groups
    by part-type using `_part_key`, averages prices within each group, and
    returns the top `n` groups by avg sold price.

    eBay groups identical *listings* server-side (so each row carries an
    `itemssold` count of repeat sales of the same listing), but two sellers
    listing the same part show up as separate rows — that's why we still need
    our own semantic dedup on top of Terapeak's results.
    """
    cookie = cookie or os.getenv("EBAY_TERAPEAK_COOKIE", "").strip()
    if not cookie:
        raise RuntimeError("EBAY_TERAPEAK_COOKIE not set")

    keywords = f"{make} {year} {model}"
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - 90 * 24 * 60 * 60 * 1000

    PAGE = 50  # Terapeak's max page size
    raw_rows: list[dict] = []

    for offset in range(0, total_listings, PAGE):
        page_rows = _terapeak_fetch_page(
            keywords, offset, PAGE, cookie, now_ms, start_ms,
        )
        if not page_rows:
            break

        for r in page_rows:
            listing = r.get("listing") or {}
            title = _terapeak_text(listing.get("title"))
            if not title or _is_new_part(title):
                continue

            avg_node = (r.get("avgsalesprice") or {}).get("avgsalesprice")
            price = _terapeak_parse_price(_terapeak_text(avg_node))
            if not price:
                continue

            action = (listing.get("title") or {}).get("action") or {}
            item_url = (action.get("URL") or "").split("?")[0]

            sold_date = _terapeak_text(r.get("datelastsold"))

            try:
                sample_count = int(_terapeak_text(r.get("itemssold")) or "1")
            except ValueError:
                sample_count = 1

            # Expand each Terapeak row into N copies (one per actual sale) so
            # the downstream `_deduplicate` averages over real sale weight,
            # not per-listing weight.
            for _ in range(max(1, sample_count)):
                raw_rows.append({
                    "title": title,
                    "price_usd": price,
                    "url": item_url,
                    "sold_date_str": sold_date,
                })

        if len(page_rows) < PAGE:
            break  # last page
        time.sleep(0.5)  # tiny pause between pages

    # Group near-identical part types together and average within each group
    grouped = _deduplicate(raw_rows, year, make, model)
    return grouped[:n]


# ---------------------------------------------------------------------------
# eBay top-sold fetch (public scrape, fallback when no Terapeak cookie)
# ---------------------------------------------------------------------------

def fetch_top_sold(year, make: str, model: str, n: int = TOP_N) -> list[dict]:
    """Search eBay sold listings for a vehicle, deduplicate, return top n by avg price."""
    query = f"{year} {make} {model} parts"  # "parts" keeps eBay in the parts domain, not whole cars
    raw: list[dict] = []

    for page in range(1, 5):
        url = (
            "https://www.ebay.com/sch/i.html"
            f"?_nkw={quote_plus(query)}"
            "&_sacat=0"
            "&LH_Sold=1&LH_Complete=1"
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

    use_terapeak = bool(os.getenv("EBAY_TERAPEAK_COOKIE", "").strip())
    if use_terapeak:
        log.info("Using Terapeak (Seller Hub Research) — authenticated, no CAPTCHA.")
        per_vehicle_delay = TERAPEAK_DELAY
    else:
        log.info("Using public eBay scrape (no EBAY_TERAPEAK_COOKIE set).")
        per_vehicle_delay = DELAY_SEC

    log.info("%d vehicle(s) need a parts refresh", total)
    batch: list[dict] = []

    for i, v in enumerate(vehicles, 1):
        label = f"{v['year']} {v['make']} {v['model']}"
        log.info("[%d/%d] %s", i, total, label)

        items: list[dict] = []
        if use_terapeak:
            try:
                items = fetch_top_sold_terapeak(v["year"], v["make"], v["model"])
            except Exception as exc:
                log.error("  Terapeak failed (%s) — falling back to scrape", exc)
        if not items and not use_terapeak:
            items = fetch_top_sold(v["year"], v["make"], v["model"])
        elif not items and use_terapeak:
            log.warning("  Terapeak returned 0 items — leaving empty (not falling back)")

        if items:
            log.info("  -> %d parts | top: %s — $%.0f (n=%d)",
                     len(items), items[0]["title"][:55],
                     items[0]["price_usd"], items[0]["sample_count"])
        else:
            log.info("  -> no results")

        batch.append({"vehicle_id": v["vehicle_id"], "items": items})

        if i < total:
            time.sleep(per_vehicle_delay)

    if args.dry_run:
        log.info("Dry run — skipping upload.")
        return

    # Save the outgoing batch so we can inspect / replay if upload fails.
    # Write to a temp file first then replace, so the file is never half-written
    # and is always valid JSON (avoids null-byte corruption from in-place overwrites).
    tmp_path = "last_batch.json.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(batch, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, "last_batch.json")
    log.info("Uploading results … (batch saved to last_batch.json)")

    for attempt in range(1, 4):
        try:
            r = requests.post(f"{server}/api/top-sold-cache", json=batch, timeout=120)
        except Exception as exc:
            log.error("Upload network error: %s", exc)
            sys.exit(1)

        if r.status_code == 503:
            wait = 60 * attempt
            log.warning("Server busy (503) — pipeline may be running. Retrying in %ds … (attempt %d/3)", wait, attempt)
            time.sleep(wait)
            continue

        if not r.ok:
            log.error("Upload failed: HTTP %d", r.status_code)
            body_preview = r.text[:1000] if r.text else "(empty body)"
            log.error("Server response body: %s", body_preview)
            log.error("Inspect last_batch.json — to retry without re-fetching, "
                      "POST it to %s/api/top-sold-cache.", server)
            sys.exit(1)

        break  # success
    else:
        log.error("Upload failed after 3 retries (server still busy). "
                  "Wait for the pipeline to finish, then POST last_batch.json manually.")
        sys.exit(1)
    log.info("Uploaded %d items.", r.json().get("stored", 0))

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
