"""Lightweight in-memory progress tracker for the scraping pipeline.

Updated by pipeline.py as the run proceeds; read by /api/progress.
Thread-safe via a simple lock.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()

_state: dict = {
    "running": False,
    "phase": None,          # "scraping" | None
    "phase_label": "",
}


def start() -> None:
    """Call at the beginning of a pipeline run (Row52 scraping phase)."""
    with _lock:
        _state.update(
            running=True,
            phase="scraping",
            phase_label="Scraping Row52…",
        )


def finish() -> None:
    """Call when the pipeline run completes (success or error)."""
    with _lock:
        _state.update(
            running=False,
            phase=None,
            phase_label="",
        )


def get() -> dict:
    with _lock:
        return dict(_state)
