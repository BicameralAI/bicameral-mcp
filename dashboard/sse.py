"""SSE broadcast queue for the live dashboard.

Maintains a list of per-connection asyncio Queues. When notify() is called,
the JSON payload is pushed to every connected client. Connections that have
disconnected are pruned lazily on the next broadcast.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class SSEBroadcaster:
    """Fan-out broadcaster for Server-Sent Events connections."""

    def __init__(self) -> None:
        self._queues: list[asyncio.Queue[str | None]] = []

    def subscribe(self) -> asyncio.Queue[str | None]:
        """Register a new SSE connection; returns its dedicated queue."""
        q: asyncio.Queue[str | None] = asyncio.Queue()
        self._queues.append(q)
        logger.debug("[dashboard] SSE client connected (%d total)", len(self._queues))
        return q

    def unsubscribe(self, q: asyncio.Queue[str | None]) -> None:
        """Remove a queue when a client disconnects."""
        try:
            self._queues.remove(q)
        except ValueError:
            pass
        logger.debug("[dashboard] SSE client disconnected (%d remaining)", len(self._queues))

    async def broadcast(self, data: str) -> None:
        """Push a data payload to all connected SSE clients."""
        dead: list[asyncio.Queue[str | None]] = []
        for q in list(self._queues):
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self.unsubscribe(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._queues)


_broadcaster: SSEBroadcaster | None = None


def get_broadcaster() -> SSEBroadcaster:
    global _broadcaster
    if _broadcaster is None:
        _broadcaster = SSEBroadcaster()
    return _broadcaster


def reset_broadcaster() -> None:
    """For tests only — replace the singleton with a fresh instance."""
    global _broadcaster
    _broadcaster = None
