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
