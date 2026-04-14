"""Tests for vocab_cache grounding reuse.

Tests _validate_cached_regions logic and vocab_cache query functions.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from handlers.ingest import _validate_cached_regions


# ── _validate_cached_regions tests ───────────────────────────────────


class _DictRow(dict):
    """Mimics sqlite3.Row — supports both dict-key and attribute access."""
    pass


def _make_code_graph(symbols: dict[str, list[dict]]):
    """Create a mock code_graph with a fake SymbolDB.

    Args:
        symbols: mapping of name -> list of rows (dicts with
                 file_path, start_line, end_line).
    """
    db = MagicMock()

    def lookup_by_name(name):
        return [_DictRow(r) for r in symbols.get(name, [])]

    db.lookup_by_name = lookup_by_name

    graph = SimpleNamespace(
        _ensure_initialized=lambda: None,
        _db=db,
    )
    return graph


class TestValidateCachedRegions:
    """Unit tests for _validate_cached_regions."""

    def test_valid_region_returned(self):
        """Region with a symbol that exists in the index is returned."""
        graph = _make_code_graph({
            "authorize": [
                {"file_path": "payments/auth.py", "start_line": 10, "end_line": 30, "type": "function"},
            ],
        })
        regions = [
            {"symbol": "authorize", "file_path": "payments/auth.py",
             "start_line": 5, "end_line": 20},
        ]
        result = _validate_cached_regions(regions, graph)
        assert len(result) == 1
        assert result[0]["symbol"] == "authorize"
        # Line numbers should be refreshed from live index
        assert result[0]["start_line"] == 10
        assert result[0]["end_line"] == 30
        assert result[0]["type"] == "function"

    def test_type_preserved_for_class_symbol(self):
        """Non-function symbol types (class, module) are preserved from the index."""
        graph = _make_code_graph({
            "PaymentService": [
                {"file_path": "payments/service.py", "start_line": 5, "end_line": 80,
                 "type": "class"},
            ],
        })
        regions = [
            {"symbol": "PaymentService", "file_path": "payments/service.py",
             "start_line": 1, "end_line": 50, "purpose": "payment processing"},
        ]
        result = _validate_cached_regions(regions, graph)
        assert len(result) == 1
        assert result[0]["type"] == "class"
        # purpose should be preserved from the cached region
        assert result[0]["purpose"] == "payment processing"

    def test_stale_region_discarded(self):
        """Region with a symbol not in the index is dropped."""
        graph = _make_code_graph({})  # empty index
        regions = [
            {"symbol": "deleted_function", "file_path": "old.py",
             "start_line": 1, "end_line": 10},
        ]
        result = _validate_cached_regions(regions, graph)
        assert result == []

    def test_partial_validity(self):
        """Only valid regions are kept; stale ones dropped."""
        graph = _make_code_graph({
            "good_func": [
                {"file_path": "a.py", "start_line": 1, "end_line": 5, "type": "function"},
            ],
            # "bad_func" is NOT in the index
        })
        regions = [
            {"symbol": "good_func", "file_path": "a.py",
             "start_line": 1, "end_line": 5},
            {"symbol": "bad_func", "file_path": "b.py",
             "start_line": 10, "end_line": 20},
        ]
        result = _validate_cached_regions(regions, graph)
        assert len(result) == 1
        assert result[0]["symbol"] == "good_func"

    def test_qualified_name_fallback(self):
        """Qualified names fall back to short name (last segment after '.')."""
        graph = _make_code_graph({
            # Full qualified name NOT in index, but short name IS
            "processPayment": [
                {"file_path": "pay.py", "start_line": 20, "end_line": 40, "type": "function"},
            ],
        })
        regions = [
            {"symbol": "PaymentService.processPayment", "file_path": "pay.py",
             "start_line": 1, "end_line": 10},
        ]
        result = _validate_cached_regions(regions, graph)
        assert len(result) == 1
        assert result[0]["start_line"] == 20  # refreshed from index

    def test_file_path_disambiguation(self):
        """When multiple symbols share a name, prefer the one in the cached file."""
        graph = _make_code_graph({
            "authorize": [
                {"file_path": "auth/oauth.py", "start_line": 5, "end_line": 15, "type": "function"},
                {"file_path": "payments/auth.py", "start_line": 50, "end_line": 60, "type": "function"},
                {"file_path": "admin/auth.py", "start_line": 100, "end_line": 110, "type": "function"},
            ],
        })
        regions = [
            {"symbol": "authorize", "file_path": "payments/auth.py",
             "start_line": 1, "end_line": 10},
        ]
        result = _validate_cached_regions(regions, graph)
        assert len(result) == 1
        assert result[0]["file_path"] == "payments/auth.py"
        assert result[0]["start_line"] == 50

    def test_file_moved_falls_back_to_first(self):
        """When the cached file no longer matches any row, use rows[0]."""
        graph = _make_code_graph({
            "authorize": [
                {"file_path": "new_location/auth.py", "start_line": 1, "end_line": 10, "type": "function"},
            ],
        })
        regions = [
            {"symbol": "authorize", "file_path": "old_location/auth.py",
             "start_line": 5, "end_line": 15},
        ]
        result = _validate_cached_regions(regions, graph)
        assert len(result) == 1
        # Should fall back to the only available row
        assert result[0]["file_path"] == "new_location/auth.py"
        assert result[0]["start_line"] == 1

    def test_empty_regions_returns_empty(self):
        graph = _make_code_graph({})
        assert _validate_cached_regions([], graph) == []

    def test_init_failure_returns_empty(self):
        """If code graph can't initialize, returns empty (cache hit discarded)."""
        graph = SimpleNamespace(
            _ensure_initialized=MagicMock(side_effect=RuntimeError("no index")),
        )
        regions = [
            {"symbol": "foo", "file_path": "x.py", "start_line": 1, "end_line": 5},
        ]
        result = _validate_cached_regions(regions, graph)
        assert result == []

    def test_region_without_symbol_key_skipped(self):
        """Regions missing the 'symbol' key are skipped."""
        graph = _make_code_graph({"foo": [
            {"file_path": "x.py", "start_line": 1, "end_line": 5},
        ]})
        regions = [
            {"file_path": "x.py", "start_line": 1, "end_line": 5},  # no symbol key
        ]
        result = _validate_cached_regions(regions, graph)
        assert result == []


# ── vocab_cache SurrealDB query tests ──────────────────────────────────


@pytest.fixture
async def ledger_client():
    """Create a fresh in-memory SurrealDB client with schema initialized."""
    from ledger.client import LedgerClient
    from ledger.schema import init_schema

    client = LedgerClient(url="memory://", ns="test", db="test_vocab")
    await client.connect()
    await init_schema(client)
    yield client


@pytest.mark.asyncio
class TestVocabCacheQueries:
    """Integration tests for lookup_vocab_cache / upsert_vocab_cache."""

    async def test_upsert_and_lookup(self, ledger_client):
        """Write a cache entry, then BM25 search for it.

        v0.4.7: lookup_vocab_cache returns (symbols, matched_query_text).
        """
        from ledger.queries import lookup_vocab_cache, upsert_vocab_cache

        symbols = [
            {"symbol": "authorize", "file_path": "auth.py",
             "start_line": 10, "end_line": 30, "type": "function"},
        ]
        await upsert_vocab_cache(ledger_client, "payment authorization flow", "repo-a", symbols)

        result, matched_query = await lookup_vocab_cache(ledger_client, "payment authorization", "repo-a")
        assert len(result) == 1
        assert result[0]["symbol"] == "authorize"
        assert result[0]["file_path"] == "auth.py"
        assert matched_query == "payment authorization flow"

    async def test_lookup_miss_different_repo(self, ledger_client):
        """Cache entry for repo-a should not match repo-b."""
        from ledger.queries import lookup_vocab_cache, upsert_vocab_cache

        symbols = [{"symbol": "foo", "file_path": "a.py",
                     "start_line": 1, "end_line": 5, "type": "function"}]
        await upsert_vocab_cache(ledger_client, "some query", "repo-a", symbols)

        result, matched_query = await lookup_vocab_cache(ledger_client, "some query", "repo-b")
        assert result == []
        assert matched_query == ""

    async def test_lookup_miss_no_match(self, ledger_client):
        """Completely unrelated query returns empty."""
        from ledger.queries import lookup_vocab_cache, upsert_vocab_cache

        symbols = [{"symbol": "foo", "file_path": "a.py",
                     "start_line": 1, "end_line": 5, "type": "function"}]
        await upsert_vocab_cache(ledger_client, "payment authorization flow", "repo-a", symbols)

        result, matched_query = await lookup_vocab_cache(ledger_client, "database migration", "repo-a")
        assert result == []
        assert matched_query == ""

    async def test_hit_count_increments(self, ledger_client):
        """Each lookup should bump hit_count."""
        from ledger.queries import lookup_vocab_cache, upsert_vocab_cache

        symbols = [{"symbol": "bar", "file_path": "b.py",
                     "start_line": 1, "end_line": 5, "type": "function"}]
        await upsert_vocab_cache(ledger_client, "webhook retry logic", "repo-a", symbols)

        # First lookup
        await lookup_vocab_cache(ledger_client, "webhook retry", "repo-a")
        # Second lookup
        await lookup_vocab_cache(ledger_client, "webhook retry", "repo-a")

        # Check hit_count via raw query
        rows = await ledger_client.query(
            "SELECT hit_count FROM vocab_cache WHERE repo = $r",
            {"r": "repo-a"},
        )
        assert rows
        # Initial upsert sets hit_count=1, each lookup adds 1 → expect 3
        assert rows[0]["hit_count"] >= 3

    async def test_upsert_overwrites_symbols(self, ledger_client):
        """Re-upserting same query+repo should update symbols."""
        from ledger.queries import lookup_vocab_cache, upsert_vocab_cache

        symbols_v1 = [{"symbol": "old_fn", "file_path": "a.py",
                        "start_line": 1, "end_line": 5, "type": "function"}]
        symbols_v2 = [{"symbol": "new_fn", "file_path": "b.py",
                        "start_line": 10, "end_line": 20, "type": "function"}]

        await upsert_vocab_cache(ledger_client, "test query text", "repo-a", symbols_v1)
        await upsert_vocab_cache(ledger_client, "test query text", "repo-a", symbols_v2)

        result, _ = await lookup_vocab_cache(ledger_client, "test query", "repo-a")
        assert len(result) == 1
        assert result[0]["symbol"] == "new_fn"
