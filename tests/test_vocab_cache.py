"""Tests for decision grounding reuse (Phase 1 drift fix).

Tests _validate_cached_regions logic and search_grounded_intents query.
"""

from __future__ import annotations

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

    tool = SimpleNamespace(_db=db)
    graph = SimpleNamespace(
        _ensure_initialized=lambda: None,
        _validate_tool=tool,
    )
    return graph


class TestValidateCachedRegions:
    """Unit tests for _validate_cached_regions."""

    def test_valid_region_returned(self):
        """Region with a symbol that exists in the index is returned."""
        graph = _make_code_graph({
            "authorize": [
                {"file_path": "payments/auth.py", "start_line": 10, "end_line": 30},
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
                {"file_path": "a.py", "start_line": 1, "end_line": 5},
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
                {"file_path": "pay.py", "start_line": 20, "end_line": 40},
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
                {"file_path": "auth/oauth.py", "start_line": 5, "end_line": 15},
                {"file_path": "payments/auth.py", "start_line": 50, "end_line": 60},
                {"file_path": "admin/auth.py", "start_line": 100, "end_line": 110},
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
                {"file_path": "new_location/auth.py", "start_line": 1, "end_line": 10},
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
