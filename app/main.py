"""FastAPI app: REST endpoints + dashboard."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, select

from . import config, scheduler
from .database import init_db, session_scope
from .models import PartEstimate, SearchRun, Vehicle

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


@app.get("/api/vehicles")
def list_vehicles(
    make: str | None = Query(None),
    min_value: float = Query(0),
    sort: str = Query("value", regex="^(value|year|added|make)$"),
    limit: int = Query(500, ge=1, le=2000),
) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(Vehicle)
        if make:
            stmt = stmt.where(Vehicle.make.ilike(make))
        if min_value > 0:
            stmt = stmt.where(Vehicle.estimated_total_value >= min_value)

        if sort == "value":
            stmt = stmt.order_by(desc(Vehicle.estimated_total_value))
        elif sort == "year":
            stmt = stmt.order_by(desc(Vehicle.year))
        elif sort == "added":
            stmt = stmt.order_by(desc(Vehicle.first_seen_at))
        elif sort == "make":
            stmt = stmt.order_by(Vehicle.make.asc(), Vehicle.model.asc())

        stmt = stmt.limit(limit)
        rows = session.scalars(stmt).all()

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
                "first_seen_at": v.first_seen_at.isoformat() if v.first_seen_at else None,
                "last_seen_at": v.last_seen_at.isoformat() if v.last_seen_at else None,
            }
            for v in rows
        ]


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
                "sample_size": p.sample_size,
                "queried_at": p.queried_at.isoformat() if p.queried_at else None,
            }
            for p in sorted(
                veh.parts,
                key=lambda x: (x.median_price_usd or 0),
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
        },
    }


# --- static / dashboard ----------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
