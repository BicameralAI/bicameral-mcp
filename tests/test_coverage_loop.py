"""Tests for the coverage loop (Phase 2 drift fix).

Tests _ground_single tier progression, BM25 caching across tiers,
and threshold relaxation in ground_mappings.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from adapters.code_locator import RealCodeLocatorAdapter


# ── Helpers ──────────────────────────────────────────────────────────


class _DictRow(dict):
    """Mimics sqlite3.Row — supports dict-key access."""
    pass


def _make_symbol_row(sid, name, file_path, start_line=1, end_line=10, sym_type="function"):
    return _DictRow(
        id=sid, name=name, qualified_name=name, file_path=file_path,
        start_line=start_line, end_line=end_line, type=sym_type,
    )


def _make_initialized_adapter():
    """Create an adapter with mocked internals for unit testing."""
    adapter = RealCodeLocatorAdapter.__new__(RealCodeLocatorAdapter)
    adapter._repo_path = "/fake/repo"
    adapter._initialized = True

    db = MagicMock()
    config = SimpleNamespace(fuzzy_threshold=80)
    adapter._validate_tool = SimpleNamespace(_db=db, _config=config)
    adapter._search_tool = MagicMock()
    adapter._neighbors_tool = MagicMock()

    return adapter, db


# ── _ground_single tests ────────────────────────────────────────────


class TestGroundSingle:
    """Unit tests for _ground_single."""

    def test_stage1_bm25_hit_above_threshold(self):
        """Stage 1 grounds when BM25 hit score >= threshold."""
        adapter, db = _make_initialized_adapter()
        sym = _make_symbol_row(1, "authorize", "payments/auth.py")
        db.lookup_by_file.return_value = [sym]

        hits = [{"file_path": "payments/auth.py", "score": 0.7}]

        with patch.object(adapter, "_validate_with_threshold", return_value=[]):
            regions = adapter._ground_single(
                "authorize payment calls", db,
                bm25_threshold=0.5, fuzzy_threshold=80, max_symbols=3,
                hits=hits,
            )

        assert len(regions) == 1
        assert regions[0]["symbol"] == "authorize"
        assert regions[0]["file_path"] == "payments/auth.py"

    def test_stage1_bm25_hit_below_threshold_skips(self):
        """Stage 1 skips when BM25 hit score < threshold."""
        adapter, db = _make_initialized_adapter()
        hits = [{"file_path": "payments/auth.py", "score": 0.3}]

        with patch.object(adapter, "_validate_with_threshold", return_value=[]):
            regions = adapter._ground_single(
                "authorize payment calls", db,
                bm25_threshold=0.5, fuzzy_threshold=80, max_symbols=3,
                hits=hits,
            )

        assert regions == []
        db.lookup_by_file.assert_not_called()

    def test_stage2_fuzzy_fallback(self):
        """Stage 2 runs when Stage 1 finds no BM25 hit."""
        adapter, db = _make_initialized_adapter()
        sym = _make_symbol_row(42, "processPayment", "pay.py", 20, 40)
        db.lookup_by_id.return_value = sym

        validated = [{"symbol_id": 42, "matched_symbol": "processPayment", "match_score": 85}]

        with patch.object(adapter, "_validate_with_threshold", return_value=validated):
            regions = adapter._ground_single(
                "process payment logic", db,
                bm25_threshold=0.5, fuzzy_threshold=80, max_symbols=5,
                hits=[],  # no BM25 hits
            )

        assert len(regions) == 1
        assert regions[0]["symbol"] == "processPayment"

    def test_max_symbols_respected(self):
        """Only max_symbols regions returned from Stage 1."""
        adapter, db = _make_initialized_adapter()
        syms = [_make_symbol_row(i, f"sym{i}", "big_file.py", i * 10, i * 10 + 5) for i in range(10)]
        db.lookup_by_file.return_value = syms

        hits = [{"file_path": "big_file.py", "score": 0.8}]

        with patch.object(adapter, "_validate_with_threshold", return_value=[]):
            regions = adapter._ground_single(
                "something about this file", db,
                bm25_threshold=0.5, fuzzy_threshold=80, max_symbols=3,
                hits=hits,
            )

        assert len(regions) == 3

    def test_empty_hits_and_no_tokens_returns_empty(self):
        """No BM25 hits and no useful tokens → empty."""
        adapter, db = _make_initialized_adapter()

        regions = adapter._ground_single(
            "the and for",  # all stop words
            db, bm25_threshold=0.5, fuzzy_threshold=80, max_symbols=3,
            hits=[],
        )

        assert regions == []


# ── ground_mappings coverage loop tests ──────────────────────────────


class TestGroundMappingsCoverageLoop:
    """Test the tier progression in ground_mappings."""

    def test_tier0_match_stops_early(self):
        """When tier 0 (strict) matches, tiers 1 and 2 are not tried."""
        adapter, db = _make_initialized_adapter()
        call_count = {"n": 0}

        def fake_ground_single(desc, db_, bm25_t, fuzzy_t, max_s, hits=None):
            call_count["n"] += 1
            if bm25_t == 0.5:  # tier 0
                return [{"symbol": "found", "file_path": "a.py",
                         "start_line": 1, "end_line": 10, "type": "function",
                         "purpose": desc}]
            return []

        with patch.object(adapter, "_ground_single", side_effect=fake_ground_single), \
             patch.object(adapter, "search_code", return_value=[]):
            resolved, deferred = adapter.ground_mappings([
                {"intent": "find this symbol", "code_regions": []},
            ])

        assert len(resolved) == 1
        assert resolved[0]["code_regions"][0]["symbol"] == "found"
        assert call_count["n"] == 1  # only tier 0 was tried

    def test_tier1_match_after_tier0_fails(self):
        """When tier 0 fails but tier 1 matches."""
        adapter, db = _make_initialized_adapter()
        tiers_tried = []

        def fake_ground_single(desc, db_, bm25_t, fuzzy_t, max_s, hits=None):
            tiers_tried.append(bm25_t)
            if bm25_t == 0.3:  # tier 1
                return [{"symbol": "weak_match", "file_path": "b.py",
                         "start_line": 1, "end_line": 5, "type": "function",
                         "purpose": desc}]
            return []

        with patch.object(adapter, "_ground_single", side_effect=fake_ground_single), \
             patch.object(adapter, "search_code", return_value=[]):
            resolved, _ = adapter.ground_mappings([
                {"intent": "some intent", "code_regions": []},
            ])

        assert resolved[0]["code_regions"][0]["symbol"] == "weak_match"
        assert tiers_tried == [0.5, 0.3]  # tier 0 then tier 1

    def test_all_tiers_fail_leaves_ungrounded(self):
        """When all 3 tiers fail, mapping stays without code_regions."""
        adapter, db = _make_initialized_adapter()
        tiers_tried = []

        def fake_ground_single(desc, db_, bm25_t, fuzzy_t, max_s, hits=None):
            tiers_tried.append(bm25_t)
            return []

        with patch.object(adapter, "_ground_single", side_effect=fake_ground_single), \
             patch.object(adapter, "search_code", return_value=[]):
            resolved, _ = adapter.ground_mappings([
                {"intent": "unmatchable intent", "code_regions": []},
            ])

        assert not resolved[0].get("code_regions")
        assert tiers_tried == [0.5, 0.3, 0.1]  # all 3 tiers

    def test_bm25_search_called_once_per_mapping(self):
        """BM25 search is called once and reused across tiers."""
        adapter, db = _make_initialized_adapter()

        with patch.object(adapter, "_ground_single", return_value=[]) as mock_gs, \
             patch.object(adapter, "search_code", return_value=[{"file_path": "x.py", "score": 0.1}]) as mock_search:
            adapter.ground_mappings([
                {"intent": "test intent", "code_regions": []},
            ])

        # search_code called exactly once
        assert mock_search.call_count == 1
        # _ground_single called 3 times (one per tier), all with same hits
        assert mock_gs.call_count == 3
        for call in mock_gs.call_args_list:
            assert call.kwargs["hits"] == [{"file_path": "x.py", "score": 0.1}]

    def test_pre_grounded_mappings_passed_through(self):
        """Mappings with existing code_regions skip the coverage loop."""
        adapter, db = _make_initialized_adapter()

        existing_regions = [{"symbol": "already", "file_path": "z.py",
                             "start_line": 1, "end_line": 5}]
        with patch.object(adapter, "search_code") as mock_search:
            resolved, _ = adapter.ground_mappings([
                {"intent": "has regions", "code_regions": existing_regions},
            ])

        assert resolved[0]["code_regions"] == existing_regions
        mock_search.assert_not_called()

    def test_bm25_search_failure_still_tries_tiers(self):
        """If BM25 search throws, tiers still run (with empty hits)."""
        adapter, db = _make_initialized_adapter()
        tiers_tried = []

        def fake_ground_single(desc, db_, bm25_t, fuzzy_t, max_s, hits=None):
            tiers_tried.append((bm25_t, hits))
            return []

        with patch.object(adapter, "_ground_single", side_effect=fake_ground_single), \
             patch.object(adapter, "search_code", side_effect=RuntimeError("index broken")):
            resolved, _ = adapter.ground_mappings([
                {"intent": "test intent", "code_regions": []},
            ])

        assert len(tiers_tried) == 3
        # All tiers received empty hits due to search failure
        for _, hits in tiers_tried:
            assert hits == []

    def test_init_failure_returns_deferred_count(self):
        """If code graph can't initialize, returns mappings unchanged with deferred count."""
        adapter = RealCodeLocatorAdapter.__new__(RealCodeLocatorAdapter)
        adapter._initialized = False

        with patch.object(adapter, "_ensure_initialized", side_effect=RuntimeError("no index")):
            resolved, deferred = adapter.ground_mappings([
                {"intent": "a", "code_regions": []},
                {"intent": "b", "code_regions": []},
                {"intent": "c", "code_regions": [{"symbol": "x"}]},
            ])

        assert deferred == 2  # only 2 without code_regions
        assert len(resolved) == 3


# ── _validate_with_threshold tests ───────────────────────────────────


class TestValidateWithThreshold:
    """Test threshold override mechanism."""

    def test_threshold_restored_after_call(self):
        """Config threshold is restored even if execute raises."""
        adapter, db = _make_initialized_adapter()
        original_threshold = adapter._validate_tool._config.fuzzy_threshold

        adapter._validate_tool.execute = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            adapter._validate_with_threshold(["test"], 60)

        assert adapter._validate_tool._config.fuzzy_threshold == original_threshold

    def test_threshold_temporarily_overridden(self):
        """During execute, the threshold is set to the requested value."""
        adapter, db = _make_initialized_adapter()
        captured_threshold = {}

        def fake_execute(args):
            captured_threshold["value"] = adapter._validate_tool._config.fuzzy_threshold
            return []

        adapter._validate_tool.execute = fake_execute
        adapter._validate_with_threshold(["test"], 60)

        assert captured_threshold["value"] == 60
        # Restored after
        assert adapter._validate_tool._config.fuzzy_threshold == 80
