"""Delivery-ID dedup cache for webhook receivers.

Webhook providers retry failed deliveries — the same payload may
arrive 2-N times within a short window. To prevent duplicate ingest,
we track delivery IDs (``X-GitHub-Delivery``, Slack's ``X-Slack-Request-Timestamp``
plus ``X-Slack-Retry-Num``, Linear's webhook ID) in a bounded LRU
keyed on ``(source, delivery_id)``.

Cache is in-process. Default TTL is 24 hours to cover GitHub's full
retry envelope (8 attempts over ~24h, with exponential backoff that
can stretch past the 15-min mark for late retries). Capacity default
1000 entries — operators handling high webhook volume should raise
this or accept that dedup is best-effort beyond that window.

Multi-process limitation: operators running multiple Bicameral
processes behind a load balancer would NOT share this cache —
each process dedups independently and the same delivery could
process N times. A shared cache (Redis, etc.) is out of scope.
The :func:`serve` banner prints a warning so the operator sees
this limitation at startup.

Thread-safe: a single ``threading.Lock`` gates the cache mutation.
The lock is held for microseconds per call; contention is acceptable
for the expected QPS of webhook receivers (single-digit per minute
for most operators).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


class DeliveryDedupCache:
    """Bounded LRU + TTL cache for webhook delivery dedup.

    Eviction: oldest entry dropped when ``max_entries`` exceeded.
    Expiry: entries older than ``ttl_seconds`` are not considered hits.

    Usage::

        cache = DeliveryDedupCache(max_entries=1000, ttl_seconds=900)
        if cache.is_duplicate("github", "abc-123-uuid"):
            # already processed — return 200 to provider to ack
            return
        cache.mark_seen("github", "abc-123-uuid")
        # ... process the delivery ...
    """

    def __init__(self, *, max_entries: int = 1000, ttl_seconds: int = 86400) -> None:
        self._max = max_entries
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # Maps (source, delivery_id) -> insertion epoch.
        self._entries: OrderedDict[tuple[str, str], float] = OrderedDict()

    def is_duplicate(self, source: str, delivery_id: str) -> bool:
        """Return True if this delivery_id has been seen for ``source`` within TTL."""
        if not delivery_id:
            return False
        key = (source, delivery_id)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            if time.time() - entry > self._ttl:
                # Expired — treat as not-duplicate AND drop the stale entry.
                self._entries.pop(key, None)
                return False
            return True

    def mark_seen(self, source: str, delivery_id: str) -> None:
        """Record that ``delivery_id`` has been processed for ``source``."""
        if not delivery_id:
            return
        key = (source, delivery_id)
        now = time.time()
        with self._lock:
            # If already present, refresh its position (it's not technically
            # an LRU since we don't bump on is_duplicate, but mark_seen
            # is the dominant write — newer write wins).
            if key in self._entries:
                self._entries.move_to_end(key)
                self._entries[key] = now
            else:
                self._entries[key] = now
                # Evict oldest beyond capacity.
                while len(self._entries) > self._max:
                    self._entries.popitem(last=False)

    def _size(self) -> int:
        """Test-only — current entry count."""
        with self._lock:
            return len(self._entries)


# Process-wide singleton. Webhook handlers reach for this directly.
_singleton: DeliveryDedupCache | None = None
_singleton_lock = threading.Lock()


def get_dedup_cache() -> DeliveryDedupCache:
    """Return the process-wide dedup cache, lazily initialized."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = DeliveryDedupCache()
    return _singleton


def _reset_for_tests() -> None:
    """Test-only — clear the singleton + any cached entries."""
    global _singleton
    with _singleton_lock:
        _singleton = None
