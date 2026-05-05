"""Lightweight in-memory progress tracker for the scraping pipeline.

Updated by pipeline.py as the run proceeds; read by /api/progress.
Thread-safe via a simple lock.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_state: dict = {
    "running": False,
    "phase": None,          # "scraping" | "pricing" | None
    "phase_label": "",
    "vehicles_total": 0,
    "vehicles_done": 0,
    "parts_queried": 0,
    "current_vehicle": None,
}


def start() -> None:
    """Call at the beginning of a pipeline run (Row52 scraping phase)."""
    with _lock:
        _state.update(
            running=True,
            phase="scraping",
            phase_label="Scraping Row52…",
            vehicles_total=0,
            vehicles_done=0,
            parts_queried=0,
            current_vehicle=None,
        )


def set_pricing(total: int) -> None:
    """Call once Row52 scraping is done and we know the total vehicle count."""
    with _lock:
        _state.update(
            phase="pricing",
            phase_label="Pricing parts on eBay…",
            vehicles_total=total,
            vehicles_done=0,
        )


def vehicle_done(label: str, parts_queried_total: int) -> None:
    """Call after each vehicle's parts have been priced."""
    with _lock:
        _state["vehicles_done"] += 1
        _state["parts_queried"] = parts_queried_total
        _state["current_vehicle"] = label


def finish() -> None:
    """Call when the pipeline run completes (success or error)."""
    with _lock:
        _state.update(
            running=False,
            phase=None,
            phase_label="",
            current_vehicle=None,
        )


def get() -> dict:
    with _lock:
        return dict(_state)
