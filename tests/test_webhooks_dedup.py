"""Tests for the webhook delivery-id dedup cache."""

from __future__ import annotations

import time
from unittest.mock import patch

from webhooks.dedup import DeliveryDedupCache, get_dedup_cache


def test_first_delivery_is_not_duplicate():
    cache = DeliveryDedupCache()
    assert cache.is_duplicate("github", "abc-123") is False


def test_marked_delivery_is_duplicate_on_second_check():
    cache = DeliveryDedupCache()
    cache.mark_seen("github", "abc-123")
    assert cache.is_duplicate("github", "abc-123") is True


def test_different_source_same_id_is_not_duplicate():
    """(source, delivery_id) is the cache key — same ID from different sources is fine."""
    cache = DeliveryDedupCache()
    cache.mark_seen("github", "abc-123")
    assert cache.is_duplicate("slack", "abc-123") is False


def test_empty_delivery_id_never_duplicates():
    """Some providers may omit delivery IDs on bad payloads — we shouldn't
    cache them and shouldn't false-positive-flag a missing ID as a dup."""
    cache = DeliveryDedupCache()
    cache.mark_seen("github", "")
    assert cache.is_duplicate("github", "") is False


def test_expired_entries_are_not_duplicates():
    """TTL behavior: an entry past the TTL is not a duplicate."""
    cache = DeliveryDedupCache(max_entries=10, ttl_seconds=60)
    with patch("webhooks.dedup.time.time", return_value=1000.0):
        cache.mark_seen("github", "old")
    # Travel 120 seconds forward.
    with patch("webhooks.dedup.time.time", return_value=1120.0):
        assert cache.is_duplicate("github", "old") is False


def test_eviction_at_max_capacity():
    """LRU-ish: oldest entry drops when capacity exceeded."""
    cache = DeliveryDedupCache(max_entries=3, ttl_seconds=60)
    cache.mark_seen("github", "a")
    cache.mark_seen("github", "b")
    cache.mark_seen("github", "c")
    cache.mark_seen("github", "d")  # evicts "a"
    assert cache.is_duplicate("github", "a") is False
    assert cache.is_duplicate("github", "b") is True
    assert cache.is_duplicate("github", "c") is True
    assert cache.is_duplicate("github", "d") is True


def test_get_dedup_cache_returns_singleton():
    from webhooks.dedup import _reset_for_tests

    _reset_for_tests()
    a = get_dedup_cache()
    b = get_dedup_cache()
    assert a is b
    _reset_for_tests()


# ── partitioned buckets ─────────────────────────────────────────────────────


def test_partition_isolates_eviction():
    """The LOW-1 property: a flood into one partition evicts only that
    partition's oldest entries — another partition's entries survive."""
    cache = DeliveryDedupCache(max_entries=2, ttl_seconds=60)
    # Partition B records one entry.
    cache.mark_seen("google_drive", "b-keep", partition="ch-B")
    # Partition A is flooded past its per-bucket cap of 2.
    cache.mark_seen("google_drive", "a1", partition="ch-A")
    cache.mark_seen("google_drive", "a2", partition="ch-A")
    cache.mark_seen("google_drive", "a3", partition="ch-A")  # evicts a1 within ch-A
    assert cache.is_duplicate("google_drive", "a1", partition="ch-A") is False
    assert cache.is_duplicate("google_drive", "a2", partition="ch-A") is True
    assert cache.is_duplicate("google_drive", "a3", partition="ch-A") is True
    # ch-B's entry is untouched by ch-A's flood.
    assert cache.is_duplicate("google_drive", "b-keep", partition="ch-B") is True


def test_partition_none_is_its_own_bucket():
    """An entry marked under a named partition is not a duplicate when
    queried with the default partition=None."""
    cache = DeliveryDedupCache()
    cache.mark_seen("google_drive", "x", partition="ch-1")
    assert cache.is_duplicate("google_drive", "x", partition=None) is False
    assert cache.is_duplicate("google_drive", "x", partition="ch-1") is True


def test_partition_distinct_channels_independent():
    """Same delivery_id under two channel partitions are independent."""
    cache = DeliveryDedupCache()
    cache.mark_seen("google_drive", "msg-7", partition="ch-A")
    assert cache.is_duplicate("google_drive", "msg-7", partition="ch-A") is True
    assert cache.is_duplicate("google_drive", "msg-7", partition="ch-B") is False


def test_max_partitions_evicts_lru_bucket():
    """Bucket COUNT is bounded: creating a 3rd bucket past max_partitions=2
    evicts the least-recently-written bucket wholesale."""
    cache = DeliveryDedupCache(max_entries=10, max_partitions=2, ttl_seconds=60)
    cache.mark_seen("google_drive", "d", partition="ch-A")
    cache.mark_seen("google_drive", "d", partition="ch-B")
    assert cache._partition_count() == 2
    cache.mark_seen("google_drive", "d", partition="ch-C")  # evicts ch-A (LRU bucket)
    assert cache._partition_count() == 2
    assert cache.is_duplicate("google_drive", "d", partition="ch-A") is False
    assert cache.is_duplicate("google_drive", "d", partition="ch-B") is True
    assert cache.is_duplicate("google_drive", "d", partition="ch-C") is True


def test_mark_seen_bumps_bucket_recency():
    """A write to an existing bucket refreshes its LRU position, so an
    actively-written bucket is not the one evicted under partition pressure."""
    cache = DeliveryDedupCache(max_entries=10, max_partitions=2, ttl_seconds=60)
    cache.mark_seen("google_drive", "d", partition="ch-A")
    cache.mark_seen("google_drive", "d", partition="ch-B")
    # Re-write ch-A — it becomes most-recently-written.
    cache.mark_seen("google_drive", "d2", partition="ch-A")
    cache.mark_seen("google_drive", "d", partition="ch-C")  # evicts ch-B, not ch-A
    assert cache.is_duplicate("google_drive", "d", partition="ch-A") is True
    assert cache.is_duplicate("google_drive", "d", partition="ch-B") is False
    assert cache.is_duplicate("google_drive", "d", partition="ch-C") is True
