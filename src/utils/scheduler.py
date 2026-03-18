"""APScheduler wrapper — periodic job management."""

from __future__ import annotations

from typing import Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.utils.logger import get_logger

log = get_logger("scheduler")

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Return the singleton scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30},
        )
    return _scheduler


def add_interval_job(
    func: Callable[..., Any],
    seconds: int,
    job_id: str | None = None,
    **kwargs: Any,
) -> None:
    """Register a repeating job."""
    scheduler = get_scheduler()
    jid = job_id or func.__name__
    scheduler.add_job(func, "interval", seconds=seconds, id=jid, replace_existing=True, **kwargs)
    log.info("scheduled_job", job_id=jid, interval_s=seconds)


def start() -> None:
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        log.info("scheduler_started")


def shutdown() -> None:
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        log.info("scheduler_stopped")
