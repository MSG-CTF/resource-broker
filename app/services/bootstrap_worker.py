import asyncio
from contextlib import suppress
import logging
import os

from app.db.session import get_session_factory
from app.services.bootstrap_job_service import BootstrapJobService


LOGGER = logging.getLogger(__name__)


def _poll_seconds() -> int:
    try:
        value = int(os.getenv("BOOTSTRAP_JOB_POLL_SECONDS", "10"))
    except ValueError:
        return 10
    return min(max(value, 2), 60)


def process_one() -> bool:
    with get_session_factory()() as session:
        return BootstrapJobService(session).process_next()


async def run_bootstrap_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await asyncio.to_thread(process_one)
        except Exception:
            LOGGER.exception("Bootstrap worker iteration failed")
            processed = False
        delay = 1 if processed else _poll_seconds()
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
