"""Lightweight in-memory progress tracker for the scraping pipeline.

Updated by pipeline.py as the run proceeds; read by /api/progress.
Thread-safe via a simple lock.

State shape returned by get():
  {
    "running": bool,
    "phase": "scraping" | "pricing" | None,
    "phase_label": str,          # e.g. "Scraping Row52..."
    "scrape_pages_done": int,
    "scrape_pages_total": int,   # 0 = unknown (indeterminate)
    "pricing_done": int,
    "pricing_total": int,        # 0 = unknown
    "current_vehicle": str,      # e.g. "2018 BMW 3 Series"
  }
"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_state: dict = {
    "running": False,
    "phase": None,
    "phase_label": "",
    "scrape_pages_done": 0,
    "scrape_pages_total": 0,
    "pricing_done": 0,
    "pricing_total": 0,
    "current_vehicle": "",
}


def start() -> None:
    """Call at the very beginning of a pipeline run."""
    with _lock:
        _state.update(
            running=True,
            phase="scraping",
            phase_label="Scraping Row52...",
            scrape_pages_done=0,
            scrape_pages_total=0,
            pricing_done=0,
            pricing_total=0,
            current_vehicle="",
        )


def scrape_page(done: int, total: int, label: str = "") -> None:
    """Call after each Row52 page is fetched."""
    with _lock:
        _state.update(
            phase="scraping",
            phase_label=label or "Scraping Row52...",
            scrape_pages_done=done,
            scrape_pages_total=total,
        )


def start_pricing(total: int) -> None:
    """Call once scraping is complete, before the pricing loop."""
    with _lock:
        _state.update(
            phase="pricing",
            phase_label=f"Pricing vehicles - 0 of {total}",
            pricing_done=0,
            pricing_total=total,
            current_vehicle="",
        )


def vehicle_pricing(done: int, total: int, vehicle_label: str) -> None:
    """Call after each vehicle's pricing pass completes."""
    with _lock:
        _state.update(
            pricing_done=done,
            pricing_total=total,
            phase_label=f"Pricing vehicles - {done} of {total}",
            current_vehicle=vehicle_label,
        )


def finish() -> None:
    """Call when the pipeline run completes (success or error)."""
    with _lock:
        _state.update(
            running=False,
            phase=None,
            phase_label="",
            current_vehicle="",
        )


def get() -> dict:
    with _lock:
        return dict(_state)
