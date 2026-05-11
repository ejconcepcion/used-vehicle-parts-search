#!/usr/bin/env python3
"""price_server.py — Server-side variant of price_locally.py.

Uses ONLY the Terapeak (Seller Hub Research) authenticated JSON endpoint.
Public eBay HTML scraping is intentionally omitted — eBay blocks datacenter
IPs at the TLS/bot-detection layer, but Terapeak is an authenticated API that
works from any IP as long as the session cookie is valid.

Set in .env (or environment):
    EBAY_TERAPEAK_COOKIE=<your Seller Hub session cookie>
    SERVER_URL=http://localhost:8000   (default; change if server runs elsewhere)
    EBAY_PROXY=http://user:pass@host:port  (optional — if eBay blocks the IP)

Run from the project root:
    python price_server.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import defaultdict
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s :: %(message)s",
)
log = logging.getLogger(__name__)

PRICE_RE       = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
NEW_PART_RE    = re.compile(r"\b(brand\s+new|new\s+in\s+box|new\s+old\s+stock|nos)\b", re.IGNORECASE)
TERAPEAK_DELAY = 1.5
TOP_N          = 50


def _is_new_part(title: str) -> bool:
    return bool(NEW_PART_RE.search(title))


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
                          cookie: str, now_ms: int, start_ms: int,
                          proxies: dict | None = None) -> list[dict]:
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
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=30)
    except Exception as exc:
        log.warning("  Terapeak request error for %r [off=%d]: %s", keywords, offset, exc)
        return []

    if not resp.ok:
        log.warning("  Terapeak HTTP %d for %r [off=%d]", resp.status_code, keywords, offset)
        if resp.status_code in (401, 403):
            log.error("  Cookie is invalid or expired — update EBAY_TERAPEAK_COOKIE")
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
                             proxies: dict | None = None,
                             total_listings: int = 100) -> list[dict]:
    """Query Terapeak for sold parts data, deduplicate, return top n by avg price."""
    cookie = cookie or os.getenv("EBAY_TERAPEAK_COOKIE", "").strip()
    if not cookie:
        raise RuntimeError("EBAY_TERAPEAK_COOKIE not set")

    keywords = f"{make} {year} {model}"
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - 90 * 24 * 60 * 60 * 1000

    PAGE = 50
    raw_rows: list[dict] = []

    for offset in range(0, total_listings, PAGE):
        page_rows = _terapeak_fetch_page(
            keywords, offset, PAGE, cookie, now_ms, start_ms, proxies=proxies,
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

            for _ in range(max(1, sample_count)):
                raw_rows.append({
                    "title": title,
                    "price_usd": price,
                    "url": item_url,
                    "sold_date_str": sold_date,
                })

        if len(page_rows) < PAGE:
            break
        time.sleep(0.5)

    grouped = _deduplicate(raw_rows, year, make, model)
    return grouped[:n]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=os.getenv("SERVER_URL", "http://localhost:8000"),
                        help="Server base URL (default: http://localhost:8000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch data but don't push to server")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only process the first N vehicles (default: all)")
    args = parser.parse_args()

    cookie = os.getenv("EBAY_TERAPEAK_COOKIE", "").strip()
    if not cookie:
        log.error("EBAY_TERAPEAK_COOKIE is not set. This script requires Terapeak access.")
        log.error("Get your cookie from https://www.ebay.com/sh/research (open DevTools → "
                  "Network tab → any XHR request → copy the 'cookie' request header value).")
        sys.exit(1)

    proxy_url = os.getenv("EBAY_PROXY", "").strip()
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    if proxies:
        log.info("Using proxy: %s", proxy_url.split("@")[-1])  # log host only, not credentials

    server = args.server.rstrip("/")
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

    log.info("Using Terapeak (Seller Hub Research) — %d vehicle(s) to price", total)
    batch: list[dict] = []

    for i, v in enumerate(vehicles, 1):
        label = f"{v['year']} {v['make']} {v['model']}"
        log.info("[%d/%d] %s", i, total, label)

        try:
            items = fetch_top_sold_terapeak(
                v["year"], v["make"], v["model"],
                cookie=cookie, proxies=proxies,
            )
        except RuntimeError as exc:
            log.error("Fatal: %s", exc)
            sys.exit(1)
        except Exception as exc:
            log.error("  Terapeak failed for %s: %s — skipping", label, exc)
            items = []

        if items:
            log.info("  -> %d parts | top: %s — $%.0f (n=%d)",
                     len(items), items[0]["title"][:55],
                     items[0]["price_usd"], items[0]["sample_count"])
        else:
            log.info("  -> no results")

        batch.append({"vehicle_id": v["vehicle_id"], "items": items})

        if i < total:
            time.sleep(TERAPEAK_DELAY)

    if args.dry_run:
        log.info("Dry run — skipping upload.")
        with open("last_batch.json", "w", encoding="utf-8") as f:
            json.dump(batch, f, indent=2, ensure_ascii=False)
        log.info("Batch saved to last_batch.json.")
        return

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
            log.warning("Server busy (503) — retrying in %ds … (attempt %d/3)", wait, attempt)
            time.sleep(wait)
            continue

        if not r.ok:
            log.error("Upload failed: HTTP %d", r.status_code)
            body_preview = r.text[:1000] if r.text else "(empty body)"
            log.error("Server response: %s", body_preview)
            log.error("To retry without re-fetching, POST last_batch.json to %s/api/top-sold-cache", server)
            sys.exit(1)

        break
    else:
        log.error("Upload failed after 3 retries. POST last_batch.json manually when server is free.")
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
