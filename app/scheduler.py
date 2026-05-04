"""APScheduler setup. The FastAPI app starts this on startup."""

from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config
from .pipeline import run_pipeline

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_run_lock = threading.Lock()


def _job() -> None:
    if not _run_lock.acquire(blocking=False):
        log.warning("Pipeline still running from previous trigger; skipping.")
        return
    try:
        log.info("Starting scheduled pipeline run")
        result = run_pipeline()
        log.info("Scheduled run done: %s", result)
    finally:
        _run_lock.release()


def start() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    sch = BackgroundScheduler(timezone="UTC")
    sch.add_job(
        _job,
        trigger=CronTrigger(
            hour=config.DAILY_RUN_HOUR,
            minute=config.DAILY_RUN_MINUTE,
        ),
        id="daily_pipeline",
        replace_existing=True,
    )
    sch.start()
    _scheduler = sch
    log.info(
        "Scheduler started; daily run at %02d:%02d",
        config.DAILY_RUN_HOUR,
        config.DAILY_RUN_MINUTE,
    )
    return sch


def trigger_now() -> bool:
    """Kick off the pipeline immediately in a background thread.
    Returns True if started, False if already running.
    """
    if _run_lock.locked():
        return False
    threading.Thread(target=_job, daemon=True).start()
    return True


def is_running() -> bool:
    return _run_lock.locked()
