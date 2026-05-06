"""Lightweight in-memory progress tracker for the scraping pipeline.

Updated by pipeline.py as the run proceeds; read by /api/progress.
Thread-safe via a simple lock.

State shape returned by get():
  {
    "running": bool,
    "phase_label": str,       # e.g. "Scraping Row52..."
    "pages_done": int,
    "pages_total": int,       # 0 = unknown (indeterminate)
  }
"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_state: dict = {
    "running": False,
    "phase_label": "",
    "pages_done": 0,
    "pages_total": 0,
}


def start() -> None:
    """Call at the very beginning of a pipeline run."""
    with _lock:
        _state.update(
            running=True,
            phase_label="Scraping Row52...",
            pages_done=0,
            pages_total=0,
        )


def scrape_page(done: int, total: int, label: str = "") -> None:
    """Call after each Row52 page is fetched."""
    with _lock:
        _state.update(
            phase_label=label or "Scraping Row52...",
            pages_done=done,
            pages_total=total,
        )


def finish() -> None:
    """Call when the pipeline run completes (success or error)."""
    with _lock:
        _state.update(
            running=False,
            phase_label="",
            pages_done=0,
            pages_total=0,
        )


def get() -> dict:
    with _lock:
        return dict(_state)
