"""Server-side Terapeak pricing.

Fetches sold-listing data from eBay Seller Hub Research (Terapeak) for
each scraped vehicle and writes results to the top_sold_part table.

Enabled by setting SERVER_SIDE_PRICING=1 and EBAY_TERAPEAK_COOKIE in .env.
Disabled by default — set SERVER_SIDE_PRICING=0 to roll back to price_locally.py.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
from collections import defaultdict
from typing import Callable
from urllib.parse import urlencode

import requests

from . import config
from .database import session_scope
from .models import TopSoldPart, Vehicle

log = logging.getLogger(__name__)

PRICE_RE    = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")
NEW_PART_RE = re.compile(r"\b(brand\s+new|new\s+in\s+box|new\s+old\s+stock|nos)\b", re.IGNORECASE)
_NOISE_RE   = re.compile(
    r"\b(?:oem|genuine|original|factory|aftermarket|fits?|for|new|used|"
    r"tested|good|working|excellent|clean|nice|rare|assy|assembly|complete|"
    r"set|pair|kit|unit|module|w|with|without|and|the|a|an|no|"
    r"lh|rh|fl|fr|rl|rr|left|right|front|rear|upper|lower|inner|outer|"
    r"driver|passenger|side|part|parts|number)\b",
    re.IGNORECASE,
)

TOP_N      = 50
PAGE_SIZE  = 50
DELAY_SEC  = 1.5   # between vehicles


# ---------------------------------------------------------------------------
# Terapeak fetch
# ---------------------------------------------------------------------------

def _terapeak_text(node) -> str:
    if not isinstance(node, dict):
        return str(node) if node else ""
    return "".join(s.get("text", "") for s in (node.get("textSpans") or [])).strip()


def _parse_price(s: str) -> float | None:
    m = PRICE_RE.search(s or "")
    return float(m.group(1).replace(",", "")) if m else None


def _fetch_page(keywords: str, offset: int, cookie: str,
                now_ms: int, start_ms: int) -> list[dict]:
    params = [
        ("marketplace", "EBAY-US"),
        ("keywords",    keywords),
        ("dayRange",    "90"),
        ("endDate",     str(now_ms)),
        ("startDate",   str(start_ms)),
        ("categoryId",  "6028"),
        ("conditionId", "3000"),
        ("offset",      str(offset)),
        ("limit",       str(PAGE_SIZE)),
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
        "user-agent":       ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/147.0.0.0 Safari/537.36"),
        "x-requested-with": "XMLHttpRequest",
        "cookie":           cookie,
    }
    # Disable system-level http_proxy/https_proxy env vars — Terapeak is
    # authenticated so no residential proxy is needed, and a misconfigured
    # system proxy causes 407 errors.
    proxies = {"http": None, "https": None}
    try:
        resp = requests.get(url, headers=headers, proxies=proxies, timeout=30)
    except Exception as exc:
        log.warning("Terapeak request error for %r [off=%d]: %s", keywords, offset, exc)
        return []
    if not resp.ok:
        log.warning("Terapeak HTTP %d for %r [off=%d]", resp.status_code, keywords, offset)
        return []

    modules = []
    for ln in resp.text.splitlines():
        ln = ln.strip()
        if ln:
            try:
                modules.append(json.loads(ln))
            except json.JSONDecodeError:
                pass

    search = next((m for m in modules if m.get("_type") == "SearchResultsModule"), None)
    if not search:
        log.warning("Terapeak: no SearchResultsModule for %r [off=%d] — cookie may be stale", keywords, offset)
        return []
    return search.get("results", []) or []


def _fetch_terapeak(year, make: str, model: str, cookie: str) -> list[dict]:
    keywords = f"{make} {year} {model}"
    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - 90 * 24 * 60 * 60 * 1000
    raw: list[dict] = []

    for offset in range(0, 200, PAGE_SIZE):
        rows = _fetch_page(keywords, offset, cookie, now_ms, start_ms)
        if not rows:
            break
        for r in rows:
            listing  = r.get("listing") or {}
            title    = _terapeak_text(listing.get("title"))
            if not title or NEW_PART_RE.search(title):
                continue
            avg_node = (r.get("avgsalesprice") or {}).get("avgsalesprice")
            price    = _parse_price(_terapeak_text(avg_node))
            if not price:
                continue
            action   = (listing.get("title") or {}).get("action") or {}
            item_url = (action.get("URL") or "").split("?")[0]
            sold_date = _terapeak_text(r.get("datelastsold"))
            try:
                sample_count = int(_terapeak_text(r.get("itemssold")) or "1")
            except ValueError:
                sample_count = 1
            for _ in range(max(1, sample_count)):
                raw.append({"title": title, "price_usd": price,
                             "url": item_url, "sold_date_str": sold_date})
        if len(rows) < PAGE_SIZE:
            break
        time.sleep(0.5)

    return _deduplicate(raw, year, make, model)[:TOP_N]


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _part_key(title: str, year, make: str, model: str) -> str:
    t = title.lower()
    for word in [str(year or ""), *make.lower().split(), *model.lower().split()]:
        if word:
            t = re.sub(r"\b" + re.escape(word) + r"\b", " ", t)
    t = _NOISE_RE.sub(" ", t)
    t = re.sub(r"[^a-z0-9\s]", " ", t).strip()
    tokens = re.sub(r"\s+", " ", t).split()[:3]
    return " ".join(tokens) if tokens else title[:20].lower()


def _deduplicate(raw: list[dict], year, make: str, model: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in raw:
        groups[_part_key(item["title"], year, make, model)].append(item)
    out = []
    for group in groups.values():
        avg   = sum(i["price_usd"] for i in group) / len(group)
        rep   = max(group, key=lambda x: x["price_usd"])
        out.append({**rep, "price_usd": round(avg, 2), "sample_count": len(group)})
    out.sort(key=lambda x: x["price_usd"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# Value calculation (shared with main.py update_top_sold_cache)
# ---------------------------------------------------------------------------

def calc_vehicle_value(parts: list) -> tuple[float, float]:
    """Return (net_total, gross_total) from a list of top-sold parts.

    Applies the standard eBay fee formula per part:
      net = price * 0.87 - 0.30 - shipping_est
    with a flat $20 shipping estimate since top-sold parts have no per-part shipping.
    """
    gross = sum(p["price_usd"] if isinstance(p, dict) else p.price_usd for p in parts)
    net   = sum(
        max(0.0, (p["price_usd"] if isinstance(p, dict) else p.price_usd) * 0.87 - 0.30 - 20.0)
        for p in parts
    )
    return round(net, 2), round(gross, 2)


# ---------------------------------------------------------------------------
# Main entry point called from pipeline.py
# ---------------------------------------------------------------------------

def run_pricing(
    vehicles: list[dict],
    on_vehicle: Callable[[int, int], None] | None = None,
) -> int:
    """Price a list of vehicles via Terapeak and persist results to DB.

    vehicles: list of dicts with keys id, year, make, model.
    on_vehicle(done, total): optional progress callback.
    Returns the number of vehicles successfully priced.
    """
    cookie = config.EBAY_TERAPEAK_COOKIE
    if not cookie:
        log.warning("SERVER_SIDE_PRICING=1 but EBAY_TERAPEAK_COOKIE not set — skipping")
        return 0

    total  = len(vehicles)
    priced = 0

    for i, v in enumerate(vehicles, 1):
        if on_vehicle:
            on_vehicle(i, total)
        label = f"{v['year']} {v['make']} {v['model']}"
        log.info("[%d/%d] Pricing %s", i, total, label)

        try:
            items = _fetch_terapeak(v["year"], v["make"] or "", v["model"] or "", cookie)
        except Exception:
            log.exception("Terapeak failed for %s", label)
            items = []

        if items:
            log.info("  -> %d parts | top: %s — $%.0f",
                     len(items), items[0]["title"][:55], items[0]["price_usd"])
        else:
            log.info("  -> no results")

        net_val, gross_val = calc_vehicle_value(items)
        now = dt.datetime.utcnow()

        with session_scope() as session:
            veh = session.get(Vehicle, v["id"])
            if veh is None:
                continue
            for old in list(veh.top_sold_parts):
                session.delete(old)
            session.flush()
            for item in items:
                session.add(TopSoldPart(
                    vehicle_id=v["id"],
                    title=item["title"],
                    price_usd=item["price_usd"],
                    url=item.get("url", ""),
                    sold_date_str=item.get("sold_date_str", ""),
                    sample_count=item.get("sample_count", 1),
                    queried_at=now,
                ))
            veh.estimated_total_value = net_val
            veh.gross_total_value     = gross_val
            priced += 1

        if i < total:
            time.sleep(DELAY_SEC)

    return priced
