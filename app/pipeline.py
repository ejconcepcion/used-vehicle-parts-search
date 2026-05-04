"""Pipeline orchestrator.

Steps for each run:
  1. Scrape every page of Row52 results in the configured radius.
  2. Filter to the configured target makes (BMW + VW by default).
  3. For each vehicle, look up sold-listing medians on eBay for each
     applicable curated part.
  4. Upsert vehicle + part rows. Recompute estimated_total_value.
  5. Append a SearchRun audit row.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import traceback
from typing import Optional

from sqlalchemy import select

from . import config
from .database import init_db, session_scope
from .models import EbayPriceCache, PartEstimate, SearchRun, Vehicle
from .parts_catalog import CatalogPart, parts_for_vehicle
from .scrapers import ebay, row52

log = logging.getLogger(__name__)


def _cached_median(session, query: str) -> Optional[tuple[float | None, int]]:
    """Return cached eBay result if present and within TTL."""
    cache_row = session.scalar(
        select(EbayPriceCache).where(EbayPriceCache.query == query)
    )
    if not cache_row:
        return None
    age = dt.datetime.utcnow() - cache_row.queried_at
    if age > dt.timedelta(days=config.EBAY_CACHE_DAYS):
        return None
    return cache_row.median_price_usd, cache_row.sample_size


def _store_cache(session, query: str, median: float | None, n: int, raw: list[float]) -> None:
    cache_row = session.scalar(
        select(EbayPriceCache).where(EbayPriceCache.query == query)
    )
    if cache_row is None:
        cache_row = EbayPriceCache(query=query)
        session.add(cache_row)
    cache_row.median_price_usd = median
    cache_row.sample_size = n
    cache_row.queried_at = dt.datetime.utcnow()
    cache_row.raw_prices_json = json.dumps(raw)


def _upsert_vehicle(session, v: dict) -> Vehicle:
    veh = session.scalar(select(Vehicle).where(Vehicle.vin == v["vin"]))
    now = dt.datetime.utcnow()
    if veh is None:
        veh = Vehicle(vin=v["vin"], first_seen_at=now)
        session.add(veh)
    veh.year = v.get("year")
    veh.make = v.get("make")
    veh.model = v.get("model")
    veh.yard_name = v.get("yard_name")
    veh.yard_address = v.get("yard_address")
    veh.row_number = v.get("row_number")
    veh.date_added_to_yard = v.get("date_added_to_yard")
    veh.image_url = v.get("image_url")
    veh.detail_url = v.get("detail_url")
    veh.last_seen_at = now
    session.flush()
    return veh


def _resolve_part_estimate(session, vehicle: Vehicle, part: CatalogPart) -> tuple[float | None, int, str]:
    query = part.query_template.format(year=vehicle.year or "", model=vehicle.model or "").strip()
    query = " ".join(query.split())  # normalize whitespace

    cached = _cached_median(session, query)
    if cached is not None:
        median, n = cached
        return median, n, query

    # Live fetch (rate-limited)
    median, n, raw = ebay.fetch_sold_median(query)
    _store_cache(session, query, median, n, raw)
    time.sleep(config.EBAY_QUERY_DELAY_SEC)
    return median, n, query


def _record_part(session, vehicle: Vehicle, part: CatalogPart, median: float | None, n: int, query: str) -> None:
    pe = session.scalar(
        select(PartEstimate).where(
            PartEstimate.vehicle_id == vehicle.id,
            PartEstimate.part_name == part.name,
        )
    )
    if pe is None:
        pe = PartEstimate(vehicle_id=vehicle.id, part_name=part.name, ebay_query=query)
        session.add(pe)
    pe.ebay_query = query
    pe.median_price_usd = median
    pe.sample_size = n
    pe.queried_at = dt.datetime.utcnow()


def run_pipeline() -> dict:
    init_db()
    started = dt.datetime.utcnow()
    seen = matched = parts_queried = 0
    error: str | None = None

    try:
        with session_scope() as session:
            run = SearchRun(started_at=started)
            session.add(run)
            session.flush()
            run_id = run.id

        for v in row52.search():
            seen += 1
            with session_scope() as session:
                vehicle = _upsert_vehicle(session, v)
                applicable = parts_for_vehicle(vehicle.make or "", vehicle.model or "")
                applicable = applicable[: config.PARTS_PER_VEHICLE_LIMIT]
                if not applicable:
                    matched += 1
                    continue

                total = 0.0
                for part in applicable:
                    median, n, query = _resolve_part_estimate(session, vehicle, part)
                    _record_part(session, vehicle, part, median, n, query)
                    parts_queried += 1
                    if median is not None:
                        total += median

                vehicle.estimated_total_value = total
                matched += 1
                log.info(
                    "Vehicle %s %s %s -> $%.0f (%d parts)",
                    vehicle.year, vehicle.make, vehicle.model, total, len(applicable),
                )

    except Exception:
        error = traceback.format_exc()
        log.exception("Pipeline failed")

    finished = dt.datetime.utcnow()
    with session_scope() as session:
        run = session.get(SearchRun, run_id)
        if run is not None:
            run.finished_at = finished
            run.vehicles_seen = seen
            run.vehicles_matched = matched
            run.parts_queried = parts_queried
            run.error = error

    return {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "vehicles_seen": seen,
        "vehicles_matched": matched,
        "parts_queried": parts_queried,
        "error": error,
    }
