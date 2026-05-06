"""Pipeline orchestrator.

Steps for each run:
  1. Scrape every page of Row52 results in the configured radius.
  2. Filter to the configured target makes (BMW + VW by default).
  3. For each vehicle, look up sold-listing medians on eBay for each
     applicable curated part.
  4. Upsert vehicle + part rows. Recompute gross and net totals.
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

from . import config, progress
from .database import init_db, session_scope
from .models import EbayPriceCache, PartEstimate, SearchRun, Vehicle
from .parts_catalog import CatalogPart, parts_for_vehicle
from .scrapers import ebay, row52

log = logging.getLogger(__name__)

# eBay fee structure (as of 2024)
_EBAY_FEE_RATE = 0.1325   # ~13.25% final value fee for auto parts
_PAYMENT_FEE_RATE = 0.03  # ~3% payment processing
_NET_MULTIPLIER = 1.0 - _EBAY_FEE_RATE - _PAYMENT_FEE_RATE  # 0.8375


def _net_value(median_usd: float | None, shipping_est: float) -> float | None:
    """Estimated take-home after eBay fees, payment fees, and shipping."""
    if median_usd is None:
        return None
    return max(0.0, median_usd * _NET_MULTIPLIER - shipping_est)


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
    pe.shipping_est_usd = part.shipping_est_usd
    pe.net_value_usd = _net_value(median, part.shipping_est_usd)
    pe.sample_size = n
    pe.queried_at = dt.datetime.utcnow()


def run_pipeline() -> dict:
    init_db()
    started = dt.datetime.utcnow()
    seen = matched = parts_queried = 0
    error: str | None = None
    run_id: int | None = None

    progress.start()
    try:
        with session_scope() as session:
            run = SearchRun(started_at=started)
            session.add(run)
            session.flush()
            run_id = run.id

        # Collect all matching vehicles first so we know the total upfront.
        # on_page callback feeds live page counts into the progress tracker.
        vehicles_raw = list(row52.search(on_page=progress.scrape_page))
        seen = len(vehicles_raw)

        progress.start_pricing(seen)

        for idx, v in enumerate(vehicles_raw):
            # Step 1: short read/write session -- upsert vehicle, check cache.
            # We close the session before making slow eBay network calls so that
            # the SQLite write lock is not held for minutes at a time (which would
            # block the web API endpoints with a "database is locked" error).
            with session_scope() as session:
                vehicle = _upsert_vehicle(session, v)
                applicable = parts_for_vehicle(vehicle.make or "", vehicle.model or "")
                applicable = applicable[: config.PARTS_PER_VEHICLE_LIMIT]
                if not applicable:
                    matched += 1
                    progress.vehicle_pricing(
                        idx + 1, seen,
                        "{} {} {}".format(vehicle.year, vehicle.make or "", vehicle.model or ""),
                    )
                    continue

                vehicle_id    = vehicle.id
                vehicle_year  = vehicle.year
                vehicle_make  = vehicle.make or ""
                vehicle_model = vehicle.model or ""
                label = "{} {} {}".format(vehicle_year, vehicle_make, vehicle_model)

                cached_results: list[tuple] = []
                parts_needing_fetch: list[CatalogPart] = []
                for part in applicable:
                    query = part.query_template.format(
                        year=vehicle_year or "", model=vehicle_model
                    ).strip()
                    query = " ".join(query.split())
                    hit = _cached_median(session, query)
                    if hit is not None:
                        median, n = hit
                        cached_results.append((part, median, n, query))
                    else:
                        parts_needing_fetch.append(part)

            # Step 2: slow eBay fetches -- NO session open.
            fetched_results: list[tuple] = []
            for part in parts_needing_fetch:
                query = part.query_template.format(
                    year=vehicle_year or "", model=vehicle_model
                ).strip()
                query = " ".join(query.split())
                median, n, raw = ebay.fetch_sold_median(query)
                fetched_results.append((part, median, n, query, raw))
                time.sleep(config.EBAY_QUERY_DELAY_SEC)

            # Step 3: short write session -- persist results.
            with session_scope() as session:
                vehicle = session.get(Vehicle, vehicle_id)
                if vehicle is None:
                    continue

                for part, median, n, query, raw in fetched_results:
                    _store_cache(session, query, median, n, raw)

                gross_total = 0.0
                net_total   = 0.0
                all_results = cached_results + [
                    (part, median, n, query)
                    for part, median, n, query, _ in fetched_results
                ]
                for part, median, n, query in all_results:
                    _record_part(session, vehicle, part, median, n, query)
                    parts_queried += 1
                    if median is not None:
                        gross_total += median
                        net = _net_value(median, part.shipping_est_usd)
                        if net is not None:
                            net_total += net

                vehicle.gross_total_value     = gross_total
                vehicle.estimated_total_value = net_total
                matched += 1
                progress.vehicle_pricing(idx + 1, seen, label)
                log.info(
                    "Vehicle %s -> gross $%.0f / net $%.0f (%d parts, %d fetched)",
                    label, gross_total, net_total, len(all_results), len(fetched_results),
                )

    except Exception:
        error = traceback.format_exc()
        log.exception("Pipeline failed")
    finally:
        progress.finish()

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
