"""Pipeline orchestrator.

Steps for each run:
  1. Scrape every page of Row52 results in the configured radius.
  2. Filter to the configured target makes (BMW + VW by default).
  3. Upsert vehicle rows into the database.
  4. Append a SearchRun audit row.

eBay pricing is NOT done here -- run price_locally.py on your local machine
to fetch sold-listing data and upload it to /api/top-sold-cache.
"""

from __future__ import annotations

import datetime as dt
import logging
import traceback

from sqlalchemy import select

from . import config, progress
from .database import init_db, session_scope
from .models import SearchRun, Vehicle
from .scrapers import row52

log = logging.getLogger(__name__)


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


def run_pipeline() -> dict:
    init_db()
    started = dt.datetime.utcnow()
    seen = 0
    error: str | None = None
    run_id: int | None = None

    progress.start()
    try:
        with session_scope() as session:
            run = SearchRun(started_at=started)
            session.add(run)
            session.flush()
            run_id = run.id

        # Scrape Row52 and upsert all matching vehicles.
        # on_page feeds live page counts into the progress tracker.
        vehicles_raw = list(row52.search(on_page=progress.scrape_page))
        seen = len(vehicles_raw)
        log.info("Scrape complete: %d vehicles found", seen)

        with session_scope() as session:
            for v in vehicles_raw:
                _upsert_vehicle(session, v)

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
            run.vehicles_matched = seen
            run.parts_queried = 0
            run.error = error

    return {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "vehicles_seen": seen,
        "vehicles_matched": seen,
        "parts_queried": 0,
        "error": error,
    }
