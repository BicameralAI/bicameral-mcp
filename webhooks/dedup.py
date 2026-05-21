"""Delivery-ID dedup cache for webhook receivers.

Webhook providers retry failed deliveries — the same payload may
arrive 2-N times within a short window. To prevent duplicate ingest,
we track delivery IDs (``X-GitHub-Delivery``, Slack's ``X-Slack-Request-Timestamp``
plus ``X-Slack-Retry-Num``, Linear's webhook ID) in bounded LRUs
keyed on ``delivery_id``.

## Partitioned buckets

The cache is a bounded map of buckets. Each bucket is an independent
bounded-LRU+TTL, keyed ``(source, partition)``:

- ``source`` — the provider (``"github"``, ``"google_drive"``, ...).
- ``partition`` — an optional sub-namespace within a source. The
  Google Drive handler passes ``partition=channel_id`` so a noisy
  channel evicts only its OWN oldest entries, never another
  channel's replay protection. The other four handlers omit
  ``partition`` (it defaults to ``None``) and share one bucket per
  source — their delivery IDs are globally unique so per-channel
  fairness does not apply.

``max_entries`` caps each bucket; ``max_partitions`` caps the bucket
COUNT (LRU eviction of a whole bucket when exceeded). Total memory
is therefore bounded at ``max_partitions * max_entries`` entries.

Default per-bucket TTL is 24 hours to cover GitHub's full retry
envelope (8 attempts over ~24h, with exponential backoff that can
stretch past the 15-min mark for late retries).

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
    """Bounded, partitioned LRU + TTL cache for webhook delivery dedup.

    The cache holds one bucket per ``(source, partition)``. Within a
    bucket: oldest entry dropped when ``max_entries`` exceeded; entries
    older than ``ttl_seconds`` are not considered hits. Across buckets:
    least-recently-written bucket dropped when ``max_partitions``
    exceeded.

    Usage::

        cache = DeliveryDedupCache(max_entries=1000, ttl_seconds=900)
        if cache.is_duplicate("github", "abc-123-uuid"):
            # already processed — return 200 to provider to ack
            return
        cache.mark_seen("github", "abc-123-uuid")
        # ... process the delivery ...

    Drive passes a partition so per-channel floods stay isolated::

        if cache.is_duplicate("google_drive", did, partition=channel_id):
            return
        cache.mark_seen("google_drive", did, partition=channel_id)
    """

    def __init__(
        self,
        *,
        max_entries: int = 1000,
        max_partitions: int = 512,
        ttl_seconds: int = 86400,
    ) -> None:
        self._max = max_entries
        self._max_partitions = max_partitions
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        # Maps (source, partition) -> bucket. Bucket maps delivery_id
        # -> insertion epoch. Outer OrderedDict gives LRU-of-buckets.
        self._buckets: OrderedDict[tuple[str, str | None], OrderedDict[str, float]] = OrderedDict()

    def is_duplicate(self, source: str, delivery_id: str, *, partition: str | None = None) -> bool:
        """Return True if ``delivery_id`` was seen for ``(source, partition)`` within TTL."""
        if not delivery_id:
            return False
        bucket_key = (source, partition)
        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                return False
            entry = bucket.get(delivery_id)
            if entry is None:
                return False
            if time.time() - entry > self._ttl:
                # Expired — treat as not-duplicate AND drop the stale entry.
                bucket.pop(delivery_id, None)
                return False
            return True

    def mark_seen(self, source: str, delivery_id: str, *, partition: str | None = None) -> None:
        """Record that ``delivery_id`` has been processed for ``(source, partition)``."""
        if not delivery_id:
            return
        bucket_key = (source, partition)
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = OrderedDict()
                self._buckets[bucket_key] = bucket
                # Evict least-recently-written bucket beyond capacity.
                while len(self._buckets) > self._max_partitions:
                    self._buckets.popitem(last=False)
            else:
                # Bucket-recency bump on write (matches the entry-level
                # policy: mark_seen is the dominant write — newer write
                # wins; is_duplicate does not bump).
                self._buckets.move_to_end(bucket_key)
            if delivery_id in bucket:
                bucket.move_to_end(delivery_id)
                bucket[delivery_id] = now
            else:
                bucket[delivery_id] = now
                # Evict oldest entry beyond per-bucket capacity.
                while len(bucket) > self._max:
                    bucket.popitem(last=False)

    def _size(self) -> int:
        """Test-only — current entry count across all buckets."""
        with self._lock:
            return sum(len(b) for b in self._buckets.values())

    def _partition_count(self) -> int:
        """Test-only — current bucket count."""
        with self._lock:
            return len(self._buckets)


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
