"""Generic worker-task lifecycle helper.

worker_loop wraps a callable in a forever-loop with per-iteration error
isolation and a fixed sleep interval. Returns the asyncio.Task so the
caller (typically the FastAPI lifespan context manager) can cancel it
on shutdown. One location for the loop pattern; Slack and Notion both
delegate here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

WorkFn = Callable[[], Awaitable[None]]


def worker_loop(name: str, interval_seconds: int, work_fn: WorkFn) -> asyncio.Task:
    async def _loop() -> None:
        while True:
            try:
                await work_fn()
            except Exception:  # noqa: BLE001
                logger.exception("[team-server] worker=%s iteration failed", name)
            await asyncio.sleep(interval_seconds)
    return asyncio.create_task(_loop(), name=f"team-server-worker-{name}")
