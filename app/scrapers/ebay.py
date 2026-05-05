"""eBay sold-listings price lookup.

Two backends:

- HTML scraping (default): hits ``ebay.com/sch/i.html?...&LH_Sold=1&LH_Complete=1``
  and extracts ``.s-item__price`` values.
- Official Browse API: enabled by setting ``EBAY_USE_API=1`` in ``.env`` and
  filling in client id / secret. Stub implementation in ``fetch_sold_via_api``.

Both return ``(median_price_usd, sample_size, raw_prices)``.

The pipeline caches results in the ``ebay_price_cache`` table, keyed by the
exact query string, so the same part query for similar vehicles is only
fetched once per cache window.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import time
from typing import Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from .. import config

log = logging.getLogger(__name__)

PRICE_RE = re.compile(r"\$([\d,]+(?:\.\d{1,2})?)")


# --------------------------------------------------------------------------
# HTML scraping backend
# --------------------------------------------------------------------------

def fetch_sold_via_html(query: str) -> Tuple[float | None, int, list[float]]:
    url = (
        "https://www.ebay.com/sch/i.html"
        f"?_nkw={quote_plus(query)}"
        "&_sacat=0"
        "&LH_Sold=1"
        "&LH_Complete=1"
        "&_ipg=60"
    )
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    log.info("eBay HTML query: %s", query)
    try:
        resp = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException as e:
        log.warning("eBay request failed for %r: %s", query, e)
        return None, 0, []

    if not resp.ok:
        log.warning("eBay returned HTTP %d for %r", resp.status_code, query)
        return None, 0, []

    soup = BeautifulSoup(resp.text, "lxml")

    prices: list[float] = []
    for item in soup.select("li.s-item"):
        # The first .s-item is often a placeholder/template — skip it if its
        # title is "Shop on eBay".
        title_tag = item.select_one(".s-item__title")
        if title_tag and "shop on ebay" in title_tag.get_text(strip=True).lower():
            continue

        price_tag = item.select_one(".s-item__price")
        if not price_tag:
            continue
        price_text = price_tag.get_text(" ", strip=True)
        # Range listings render as "$120.00 to $260.00" — take the midpoint.
        nums = [float(m.group(1).replace(",", "")) for m in PRICE_RE.finditer(price_text)]
        if not nums:
            continue
        if len(nums) == 1:
            prices.append(nums[0])
        else:
            prices.append(sum(nums) / len(nums))

        if len(prices) >= config.EBAY_RESULTS_PER_QUERY:
            break

    if not prices:
        return None, 0, []
    median = statistics.median(prices)
    return float(median), len(prices), prices


# --------------------------------------------------------------------------
# Official Browse API backend (stub — enable with EBAY_USE_API=1)
# --------------------------------------------------------------------------

_OAUTH_TOKEN_CACHE: dict[str, tuple[float, str]] = {}


def _ebay_oauth_token() -> str | None:
    """Get an application access token via client-credentials flow.

    Tokens last ~2 hours; we cache in-process.
    """
    if not (config.EBAY_CLIENT_ID and config.EBAY_CLIENT_SECRET):
        return None

    cached = _OAUTH_TOKEN_CACHE.get("token")
    now = time.time()
    if cached and cached[0] > now + 60:
        return cached[1]

    resp = requests.post(
        f"{config.EBAY_API_BASE}/identity/v1/oauth2/token",
        auth=(config.EBAY_CLIENT_ID, config.EBAY_CLIENT_SECRET),
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        },
        timeout=30,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body["access_token"]
    expires_at = now + int(body.get("expires_in", 7200))
    _OAUTH_TOKEN_CACHE["token"] = (expires_at, token)
    return token


def fetch_sold_via_api(query: str) -> Tuple[float | None, int, list[float]]:
    """Stub for eBay Browse API.

    Note: the Browse API only returns *active* listings — true sold-listing
    history requires the (deprecated) Finding API or the Marketplace
    Insights API (limited access). For most personal use, scraping is the
    practical path; this stub demonstrates where to swap in.
    """
    token = _ebay_oauth_token()
    if not token:
        log.warning("EBAY_USE_API=1 but client credentials are missing.")
        return None, 0, []

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(
        f"{config.EBAY_API_BASE}/buy/browse/v1/item_summary/search",
        params={"q": query, "limit": str(config.EBAY_RESULTS_PER_QUERY)},
        headers=headers,
        timeout=30,
    )
    if not resp.ok:
        log.warning("eBay API HTTP %d for %r", resp.status_code, query)
        return None, 0, []

    items = resp.json().get("itemSummaries", []) or []
    prices: list[float] = []
    for item in items:
        price = item.get("price", {}).get("value")
        if price is not None:
            try:
                prices.append(float(price))
            except (TypeError, ValueError):
                continue
    if not prices:
        return None, 0, []
    return float(statistics.median(prices)), len(prices), prices


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def fetch_sold_median(query: str) -> Tuple[float | None, int, list[float]]:
    """Front-door. Returns (median_usd, sample_size, raw_prices)."""
    if config.EBAY_USE_API:
        return fetch_sold_via_api(query)
    return fetch_sold_via_html(query)
