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


def _build_url(zip_code: str, distance: int, page: int = 1) -> str:
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
            "LocationId": 0,
            "YMMorVIN": "YMM",
            "IsVin": "false",
        }
    )
    return f"{SEARCH_URL}?{qs}"


def _meta(item: Tag, prop: str) -> str | None:
    tag = item.find("meta", attrs={"itemprop": prop})
    return tag.get("content").strip() if tag and tag.get("content") else None


def _parse_total_pages(soup: BeautifulSoup) -> int:
    """Return the total number of result pages, or 1 if unknown."""
    text = soup.get_text(" ", strip=True)
    # Row52 prints "Page X of Y" in the pager.
    m = re.search(r"of\s+(\d+)", text)
    if not m:
        return 1
    total_results = int(m.group(1))
    # 30 results per page on Row52.
    return max(1, (total_results + 29) // 30)


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
    link = block.find("a", attrs={"itemprop": "url"})
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

    # Row & date_added — these sit under <h4>Row</h4> and <h4>Added to yard</h4>
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


def search(
    zip_code: str = config.ZIP_CODE,
    distance: int = config.RADIUS_MILES,
    target_makes: list[str] | None = None,
    max_pages: int = 25,
) -> Iterator[dict]:
    """Yield matching vehicles. Filters by `target_makes` (case-insensitive)."""

    targets = {m.upper() for m in (target_makes or config.TARGET_MAKES)}
    session = requests.Session()
    session.headers["User-Agent"] = config.USER_AGENT

    # First page tells us how many pages to fetch.
    first_url = _build_url(zip_code, distance, page=1)
    log.info("Fetching %s", first_url)
    resp = session.get(first_url, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    total_pages = min(_parse_total_pages(soup), max_pages)
    log.info("Total pages: %d", total_pages)

    yield from _yield_matches(soup, targets)

    for page in range(2, total_pages + 1):
        time.sleep(config.ROW52_PAGE_DELAY_SEC)
        url = _build_url(zip_code, distance, page=page)
        log.info("Fetching %s", url)
        resp = session.get(url, timeout=30)
        if not resp.ok:
            log.warning("Page %d returned HTTP %d, stopping", page, resp.status_code)
            break
        soup = BeautifulSoup(resp.text, "lxml")
        yield from _yield_matches(soup, targets)


def _yield_matches(soup: BeautifulSoup, targets: set[str]) -> Iterator[dict]:
    for block in soup.find_all(attrs={"itemtype": "http://schema.org/Thing/Automobile"}):
        v = _parse_vehicle(block)
        if not v:
            continue
        if v["make"] and v["make"].upper() in targets:
            yield v
