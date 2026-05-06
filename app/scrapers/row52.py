"""Row52 search scraper.

Row52's results page is rendered server-side with schema.org Automobile
microdata. We pull every page in the radius and emit one dict per vehicle.

Polite: 2-second delay between page fetches, identifying User-Agent.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Iterator
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup, Tag

from .. import config

log = logging.getLogger(__name__)

SEARCH_URL = "https://row52.com/Search"
ROW52_HOME = "https://row52.com"


def _build_url(zip_code: str, distance: int, page: int = 1, location_id: int = 0) -> str:
    qs = urlencode(
        {
            "Page": page,
            "MakeId": 0,
            "ModelId": 0,
            "Year": "",
            "SortDirection": "desc",
            "Distance": distance,
            "ZipCode": zip_code,
            "Sort": "DateAdded",
            "HasImage": "",
            "HasComment": "",
            "LocationId": location_id,
            "YMMorVIN": "YMM",
            "IsVin": "false",
        }
    )
    return f"{SEARCH_URL}?{qs}"


def _meta(item: Tag, prop: str) -> str | None:
    tag = item.find("meta", attrs={"itemprop": prop})
    return tag.get("content").strip() if tag and tag.get("content") else None


def _parse_total_pages(soup: BeautifulSoup) -> int:
    """Return the total number of result pages, or 1 if unknown.

    Row52 renders a pager that says "of <N>" where N is the total *page* count
    (not the total result count). Earlier code mistakenly divided N by 30,
    producing a page count roughly 30x too small.
    """
    text = soup.get_text(" ", strip=True)
    # Row52 pager text: "... of 491 Vehicle Info ..."
    m = re.search(r"of\s+(\d+)", text)
    if not m:
        return 1
    return max(1, int(m.group(1)))


def _parse_vehicle(block: Tag) -> dict | None:
    vin = _meta(block, "vin")
    if not vin:
        return None

    make = _meta(block, "make") or ""
    model = _meta(block, "model") or ""
    year_str = _meta(block, "year") or ""
    try:
        year = int(year_str)
    except ValueError:
        year = None

    # Image
    img = block.find("img", attrs={"itemprop": "image"})
    image_url = img["src"] if img and img.get("src") else None

    # Detail link (already canonical: /Vehicle/Index/{VIN})
    # Match by href pattern to avoid picking up the yard's itemprop="url" link,
    # which also lives inside this block.
    link = block.find("a", href=re.compile(r"/Vehicle/Index/", re.I))
    detail_url = None
    if link and link.get("href"):
        href = link["href"]
        detail_url = href if href.startswith("http") else ROW52_HOME + href

    # Yard
    yard_block = block.find(attrs={"itemtype": "http://schema.org/AutomotiveBusiness"})
    yard_name = None
    yard_address = None
    if yard_block:
        name_tag = yard_block.find(attrs={"itemprop": "name"})
        addr_tag = yard_block.find(attrs={"itemprop": "address"})
        yard_name = name_tag.get_text(strip=True) if name_tag else None
        yard_address = addr_tag.get_text(strip=True) if addr_tag else None

    # Row & date_added
    row_number = None
    date_added = None
    for h4 in block.find_all("h4", class_="mobile-title"):
        label = h4.get_text(strip=True).lower()
        sibling_strong = h4.find_next("strong")
        value = sibling_strong.get_text(strip=True) if sibling_strong else None
        if label == "row":
            row_number = value
        elif label.startswith("added"):
            date_added = value

    return {
        "vin": vin,
        "year": year,
        "make": make,
        "model": model,
        "yard_name": yard_name,
        "yard_address": yard_address,
        "row_number": row_number,
        "date_added_to_yard": date_added,
        "image_url": image_url,
        "detail_url": detail_url,
    }


def _fetch_pages(
    session: requests.Session,
    targets: set[str],
    zip_code: str,
    distance: int,
    location_id: int = 0,
    max_pages: int = 25,
    seen_vins: set[str] | None = None,
) -> Iterator[dict]:
    """Fetch all pages for a given search (ZIP+radius or specific locationId).

    Deduplicates by VIN using `seen_vins` so callers can pass the same set
    across multiple search passes and avoid yielding the same vehicle twice.
    """
    if seen_vins is None:
        seen_vins = set()

    label = f"LocationId={location_id}" if location_id else f"ZIP={zip_code} r={distance}mi"
    first_url = _build_url(zip_code, distance, page=1, location_id=location_id)
    log.info("[%s] Fetching %s", label, first_url)
    resp = session.get(first_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    total_pages = min(_parse_total_pages(soup), max_pages)
    log.info("[%s] Total pages: %d", label, total_pages)

    for v in _yield_matches(soup, targets):
        if v["vin"] not in seen_vins:
            seen_vins.add(v["vin"])
            yield v

    for page in range(2, total_pages + 1):
        time.sleep(config.ROW52_PAGE_DELAY_SEC)
        url = _build_url(zip_code, distance, page=page, location_id=location_id)
        log.info("[%s] Fetching %s", label, url)
        resp = session.get(url, timeout=30)
        if not resp.ok:
            log.warning("[%s] Page %d returned HTTP %d, stopping", label, page, resp.status_code)
            break
        soup = BeautifulSoup(resp.text, "lxml")
        for v in _yield_matches(soup, targets):
            if v["vin"] not in seen_vins:
                seen_vins.add(v["vin"])
                yield v


def search(
    zip_code: str = config.ZIP_CODE,
    distance: int = config.RADIUS_MILES,
    target_makes: list[str] | None = None,
    extra_location_ids: list[int] | None = None,
    max_pages: int = 25,
) -> Iterator[dict]:
    """Yield matching vehicles. Filters by `target_makes` (case-insensitive).

    Row52's ZIP+radius search silently omits some newer yards (e.g. American
    Canyon, locationId 10798). Pass their IDs via `extra_location_ids` (or set
    EXTRA_LOCATION_IDS in config) to fetch them in a dedicated second pass.
    Duplicates across passes are suppressed by VIN.
    """
    targets = {m.upper() for m in (target_makes or config.TARGET_MAKES)}
    extra_ids = extra_location_ids if extra_location_ids is not None else config.EXTRA_LOCATION_IDS
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT

    seen_vins: set[str] = set()

    # Primary search: all yards within ZIP+radius.
    yield from _fetch_pages(session, targets, zip_code, distance,
                            location_id=0, max_pages=max_pages, seen_vins=seen_vins)

    # Supplemental passes for yards Row52 geo-search misses.
    for loc_id in extra_ids:
        log.info("Starting supplemental search for locationId=%d", loc_id)
        yield from _fetch_pages(session, targets, zip_code, distance,
                                location_id=loc_id, max_pages=max_pages, seen_vins=seen_vins)


def _yield_matches(soup: BeautifulSoup, targets: set[str]) -> Iterator[dict]:
    for block in soup.find_all(attrs={"itemtype": "http://schema.org/Thing/Automobile"}):
        v = _parse_vehicle(block)
        if not v:
            continue
        if v["make"] and v["make"].upper() in targets:
            yield v
