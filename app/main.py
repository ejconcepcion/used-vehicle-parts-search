"""FastAPI app: REST endpoints + dashboard."""

from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import delete, desc, func, select

from . import config, progress, scheduler
from .database import init_db, session_scope
from .models import EbayPriceCache, PartEstimate, SearchRun, TopSoldPart, Vehicle
from .parts_catalog import parts_for_vehicle
from .pricer import calc_vehicle_value

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    init_db()
    scheduler.start()
    yield


app = FastAPI(title="Used Vehicle Parts Search", lifespan=lifespan)


def _parse_yard_date(date_str: str | None) -> dt.date | None:
    """Parse Row52 date_added_to_yard string to a date.

    Row52 renders dates as 'Apr 28, 2026' (%b %d, %Y).
    Returns None if the string is missing or unparseable.
    """
    if not date_str:
        return None
    try:
        return dt.datetime.strptime(date_str.strip(), "%b %d, %Y").date()
    except ValueError:
        return None


@app.get("/api/vehicles")
def list_vehicles(
    make: str | None = Query(None),
    yard: str | None = Query(None),
    min_value: float = Query(0),
    sort: str = Query("added", regex="^(year|added|make)$"),
    limit: int = Query(500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    cutoff_date = dt.date.today() - dt.timedelta(days=14)

    with session_scope() as session:
        stmt = select(Vehicle)
        if make:
            stmt = stmt.where(Vehicle.make.ilike(make))
        if yard:
            stmt = stmt.where(Vehicle.yard_name == yard)
        if min_value > 0:
            stmt = stmt.where(Vehicle.estimated_total_value >= min_value)

        # Year filter — also excludes NULL year rows
        stmt = stmt.where(Vehicle.year >= 2005)

        # Pre-filter by first_seen_at so the LIMIT doesn't cut off recent vehicles
        # before the Python date filter runs. 2-day buffer covers clock skew.
        sql_cutoff = dt.datetime.utcnow() - dt.timedelta(days=16)
        stmt = stmt.where(Vehicle.first_seen_at >= sql_cutoff)

        if sort == "year":
            stmt = stmt.order_by(desc(Vehicle.year))
        elif sort == "make":
            stmt = stmt.order_by(Vehicle.make.asc(), Vehicle.model.asc())
        # "added" sort is applied in Python below after date parsing

        stmt = stmt.limit(limit)
        rows = session.scalars(stmt).all()

        # Precise date filter on date_added_to_yard (string field from Row52).
        # Vehicles with missing/unparseable dates are included (fail open).
        rows = [
            v for v in rows
            if (_parse_yard_date(v.date_added_to_yard) or dt.date.today()) >= cutoff_date
        ]

        # Sort by yard add date in Python (date_added_to_yard is a string field).
        if sort == "added":
            rows = sorted(
                rows,
                key=lambda v: _parse_yard_date(v.date_added_to_yard) or dt.date.min,
                reverse=True,
            )

        return [
            {
                "id": v.id,
                "vin": v.vin,
                "year": v.year,
                "make": v.make,
                "model": v.model,
                "yard_name": v.yard_name,
                "yard_address": v.yard_address,
                "row_number": v.row_number,
                "date_added_to_yard": v.date_added_to_yard,
                "image_url": v.image_url,
                "detail_url": v.detail_url,
                "estimated_total_value": v.estimated_total_value or 0,
                "gross_total_value": v.gross_total_value or 0,
                "first_seen_at": v.first_seen_at.isoformat() if v.first_seen_at else None,
                "last_seen_at": v.last_seen_at.isoformat() if v.last_seen_at else None,
            }
            for v in rows
        ]


@app.get("/api/yards")
def list_yards() -> list[str]:
    """Distinct yard names present in the vehicle table, alphabetically sorted."""
    with session_scope() as session:
        stmt = (
            select(Vehicle.yard_name)
            .where(Vehicle.yard_name.is_not(None))
            .distinct()
            .order_by(Vehicle.yard_name.asc())
        )
        return [name for name in session.scalars(stmt).all() if name]


@app.get("/api/vehicles/{vehicle_id}/parts")
def vehicle_parts(vehicle_id: int) -> list[dict[str, Any]]:
    with session_scope() as session:
        veh = session.get(Vehicle, vehicle_id)
        if veh is None:
            raise HTTPException(404, "Vehicle not found")
        return [
            {
                "part_name": p.part_name,
                "ebay_query": p.ebay_query,
                "median_price_usd": p.median_price_usd,
                "net_value_usd": p.net_value_usd,
                "shipping_est_usd": p.shipping_est_usd,
                "sample_size": p.sample_size,
                "queried_at": p.queried_at.isoformat() if p.queried_at else None,
            }
            for p in sorted(
                veh.parts,
                key=lambda x: (x.net_value_usd or 0),
                reverse=True,
            )
        ]


@app.get("/api/vehicles/{vehicle_id}/top-parts")
def vehicle_top_parts(vehicle_id: int) -> list[dict[str, Any]]:
    with session_scope() as session:
        veh = session.get(Vehicle, vehicle_id)
        if veh is None:
            raise HTTPException(404, "Vehicle not found")
        return [
            {
                "title": p.title,
                "price_usd": p.price_usd,
                "url": p.url,
                "sold_date_str": p.sold_date_str,
                "sample_count": p.sample_count or 1,
                "queried_at": p.queried_at.isoformat() if p.queried_at else None,
            }
            for p in sorted(
                veh.top_sold_parts,
                key=lambda x: (x.price_usd or 0),
                reverse=True,
            )
        ]


@app.get("/api/runs")
def list_runs(limit: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.scalars(
            select(SearchRun).order_by(desc(SearchRun.started_at)).limit(limit)
        ).all()
        return [
            {
                "id": r.id,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "vehicles_seen": r.vehicles_seen,
                "vehicles_matched": r.vehicles_matched,
                "parts_queried": r.parts_queried,
                "error": r.error,
            }
            for r in rows
        ]


@app.get("/api/pending-queries")
def pending_queries() -> dict[str, Any]:
    """Return eBay queries that are missing or stale -- used by the local pricer script."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(days=config.EBAY_CACHE_DAYS)
    with session_scope() as session:
        vehicles = session.scalars(select(Vehicle)).all()
        seen: set[str] = set()
        pending: list[str] = []
        for v in vehicles:
            applicable = parts_for_vehicle(v.make or "", v.model or "")
            applicable = applicable[: config.PARTS_PER_VEHICLE_LIMIT]
            for part in applicable:
                query = part.query_template.format(
                    year=v.year or "", model=v.model or ""
                ).strip()
                query = " ".join(query.split())
                if query in seen:
                    continue
                seen.add(query)
                cache_row = session.scalar(
                    select(EbayPriceCache).where(EbayPriceCache.query == query)
                )
                if cache_row is None or cache_row.queried_at < cutoff:
                    pending.append(query)
    return {"queries": pending, "total": len(pending)}


class EbayCacheItem(BaseModel):
    query: str
    median_price_usd: float | None
    sample_size: int
    raw_prices: list[float] = []


@app.post("/api/ebay-cache")
def update_ebay_cache(items: list[EbayCacheItem]) -> dict[str, Any]:
    """Accept eBay price results from the local pricer and store in cache."""
    now = dt.datetime.utcnow()
    with session_scope() as session:
        for item in items:
            cache_row = session.scalar(
                select(EbayPriceCache).where(EbayPriceCache.query == item.query)
            )
            if cache_row is None:
                cache_row = EbayPriceCache(query=item.query)
                session.add(cache_row)
            cache_row.median_price_usd = item.median_price_usd
            cache_row.sample_size = item.sample_size
            cache_row.raw_prices_json = json.dumps(item.raw_prices)
            cache_row.queried_at = now
    return {"stored": len(items)}


@app.get("/api/pending-top-sold")
def pending_top_sold() -> dict[str, Any]:
    """Return vehicles whose top-sold data is missing or older than 24 h."""
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=24)
    with session_scope() as session:
        vehicles = session.scalars(select(Vehicle)).all()
        pending = []
        for v in vehicles:
            if not v.top_sold_parts:
                pending.append({"vehicle_id": v.id, "year": v.year, "make": v.make, "model": v.model})
                continue
            newest = max(
                (p.queried_at for p in v.top_sold_parts if p.queried_at),
                default=None,
            )
            if newest is None or newest < cutoff:
                pending.append({"vehicle_id": v.id, "year": v.year, "make": v.make, "model": v.model})
    return {"vehicles": pending, "total": len(pending)}


class TopSoldItem(BaseModel):
    title: str
    price_usd: float
    url: str = ""
    sold_date_str: str = ""
    sample_count: int = 1


class TopSoldBatchEntry(BaseModel):
    vehicle_id: int
    items: list[TopSoldItem]


@app.post("/api/top-sold-cache")
def update_top_sold_cache(batch: list[TopSoldBatchEntry]) -> dict[str, Any]:
    """Accept top-sold results from the local pricer and store them."""
    import sqlalchemy.exc
    now = dt.datetime.utcnow()
    total_stored = 0
    try:
        with session_scope() as session:
            for entry in batch:
                veh = session.get(Vehicle, entry.vehicle_id)
                if veh is None:
                    log.warning("top-sold-cache: vehicle_id=%d not found, skipping", entry.vehicle_id)
                    continue
                for old in list(veh.top_sold_parts):
                    session.delete(old)
                session.flush()
                for item in entry.items:
                    session.add(TopSoldPart(
                        vehicle_id=entry.vehicle_id,
                        title=item.title,
                        price_usd=item.price_usd,
                        url=item.url,
                        sold_date_str=item.sold_date_str,
                        sample_count=item.sample_count,
                        queried_at=now,
                    ))
                total_stored += len(entry.items)

                # Update vehicle estimated value from the new top-sold data.
                net_val, gross_val = calc_vehicle_value(
                    [{"price_usd": i.price_usd} for i in entry.items]
                )
                veh.estimated_total_value = net_val
                veh.gross_total_value     = gross_val
    except sqlalchemy.exc.OperationalError as exc:
        msg = str(exc)
        if "database is locked" in msg:
            log.warning("top-sold-cache: database locked -- pipeline may be running")
            raise HTTPException(
                status_code=503,
                detail="Database is locked -- the pipeline is likely running. Retry in a minute.",
            )
        log.exception("top-sold-cache: database error")
        raise HTTPException(status_code=500, detail=f"Database error: {msg}")
    except Exception as exc:
        log.exception("top-sold-cache: unexpected error")
        raise HTTPException(status_code=500, detail=f"Unexpected error: {exc}")
    log.info("Top-sold cache updated: %d items across %d vehicles", total_stored, len(batch))
    return {"stored": total_stored}


@app.post("/api/clear-cache")
def clear_cache() -> dict[str, Any]:
    """Delete all eBay price cache and top-sold rows so the next run re-fetches everything."""
    with session_scope() as session:
        price_rows = session.execute(delete(EbayPriceCache)).rowcount
        top_rows   = session.execute(delete(TopSoldPart)).rowcount
    log.info("Cache cleared: %d price rows, %d top-sold rows", price_rows, top_rows)
    return {"cleared_price_cache": price_rows, "cleared_top_sold": top_rows}


@app.post("/api/clear-all")
def clear_all() -> dict[str, Any]:
    """Delete all vehicles (and cascade to parts/top-sold) plus all cache and run log rows."""
    with session_scope() as session:
        price_rows   = session.execute(delete(EbayPriceCache)).rowcount
        top_rows     = session.execute(delete(TopSoldPart)).rowcount
        part_rows    = session.execute(delete(PartEstimate)).rowcount
        run_rows     = session.execute(delete(SearchRun)).rowcount
        vehicle_rows = session.execute(delete(Vehicle)).rowcount
    log.info(
        "Full DB clear: %d vehicles, %d parts, %d top-sold, %d price-cache, %d runs",
        vehicle_rows, part_rows, top_rows, price_rows, run_rows,
    )
    return {
        "cleared_vehicles": vehicle_rows,
        "cleared_parts": part_rows,
        "cleared_top_sold": top_rows,
        "cleared_price_cache": price_rows,
        "cleared_runs": run_rows,
    }


@app.get("/api/progress")
def get_progress() -> dict[str, Any]:
    return progress.get()


@app.post("/api/run-now")
def run_now() -> JSONResponse:
    started = scheduler.trigger_now()
    return JSONResponse(
        {"started": started, "running": scheduler.is_running()},
        status_code=202 if started else 409,
    )


@app.get("/api/status")
def status() -> dict[str, Any]:
    with session_scope() as session:
        last = session.scalars(
            select(SearchRun).order_by(desc(SearchRun.started_at)).limit(1)
        ).first()
        total = session.scalar(select(func.count()).select_from(Vehicle)) or 0
        last_run_data = (
            {
                "started_at": last.started_at.isoformat() if last and last.started_at else None,
                "finished_at": last.finished_at.isoformat() if last and last.finished_at else None,
                "vehicles_seen": last.vehicles_seen if last else None,
                "vehicles_matched": last.vehicles_matched if last else None,
                "parts_queried": last.parts_queried if last else None,
                "error": last.error if last else None,
            }
            if last
            else None
        )
    return {
        "running": scheduler.is_running(),
        "vehicle_count": total,
        "last_run": last_run_data,
        "config": {
            "zip_code": config.ZIP_CODE,
            "radius_miles": config.RADIUS_MILES,
            "target_makes": config.TARGET_MAKES,
            "min_vehicle_value": config.MIN_VEHICLE_VALUE,
            "ebay_use_api": config.EBAY_USE_API,
            "daily_run_hour": config.DAILY_RUN_HOUR,
            "server_side_pricing": config.SERVER_SIDE_PRICING,
        },
    }


# --- static / dashboard ----------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
