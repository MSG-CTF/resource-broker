import asyncio
from contextlib import suppress
import logging

from app.db.session import get_session_factory
from app.services.windows_worker_service import (
    WindowsWorkerService,
    WindowsWorkerSettings,
)


LOGGER = logging.getLogger(__name__)


def process_one() -> bool:
    with get_session_factory()() as session:
        return WindowsWorkerService(session).process_next()


async def run_windows_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            processed = await asyncio.to_thread(process_one)
            delay = (
                1
                if processed
                else WindowsWorkerSettings.from_environment().poll_seconds
            )
        except Exception:
            LOGGER.exception("Windows worker iteration failed")
            delay = 10
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
