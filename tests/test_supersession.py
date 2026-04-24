"""Unit tests for _find_overlap_candidates — the BM25 supersession scan
that fires on every decision at ingest time.

Verifies:
- Empty BM25 result → empty candidate list (no-op, doesn't crash)
- BM25 exception → empty list (must never break ingest)
- Self-match (exact description equality, case-insensitive) → filtered out
- top_k cap is enforced
- min_confidence threshold (raised to 0.4 in v0.6.2) passes correct value
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.ingest import _find_overlap_candidates


@pytest.mark.asyncio
async def test_supersession_returns_empty_when_bm25_returns_nothing():
    ledger = MagicMock()
    ledger.search_by_query = AsyncMock(return_value=[])
    result = await _find_overlap_candidates("some new decision", ledger)
    assert result == []


@pytest.mark.asyncio
async def test_supersession_swallows_bm25_exception():
    """BM25 failures must not break ingest — the scan is purely additive."""
    ledger = MagicMock()
    ledger.search_by_query = AsyncMock(side_effect=RuntimeError("db down"))
    result = await _find_overlap_candidates("anything", ledger)
    assert result == []


@pytest.mark.asyncio
async def test_supersession_filters_self_match_case_insensitive():
    """A row with description matching the query verbatim is the decision
    itself — exclude it from the supersession candidate list."""
    ledger = MagicMock()
    ledger.search_by_query = AsyncMock(return_value=[
        {"decision_id": "decision:self", "description": "Auth USES JWT", "score": 1.0},
        {"decision_id": "decision:other", "description": "Use JWT everywhere", "score": 0.7},
    ])
    result = await _find_overlap_candidates("auth uses jwt", ledger)
    assert len(result) == 1
    assert result[0].decision_id == "decision:other"


@pytest.mark.asyncio
async def test_supersession_caps_at_top_k():
    """top_k=3 must return at most 3 candidates even if BM25 returns more."""
    ledger = MagicMock()
    rows = [
        {"decision_id": f"decision:d{i}", "description": f"Other decision {i}", "score": 0.8}
        for i in range(5)
    ]
    ledger.search_by_query = AsyncMock(return_value=rows)
    result = await _find_overlap_candidates("original query", ledger, top_k=3)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_supersession_passes_min_confidence_0_4_to_bm25():
    """Regression: v0.6.2 raised min_confidence from 0.1 to 0.4 to cut noise.

    The caller-LLM sees only high-confidence overlap candidates; low-score
    BM25 hits that would flood ingest responses are filtered server-side.
    """
    ledger = MagicMock()
    ledger.search_by_query = AsyncMock(return_value=[])
    await _find_overlap_candidates("some query", ledger)
    ledger.search_by_query.assert_called_once()
    _, kwargs = ledger.search_by_query.call_args
    assert kwargs.get("min_confidence") == 0.4
