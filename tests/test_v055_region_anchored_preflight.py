"""Region-anchored preflight retrieval tests.

Verifies that preflight surfaces decisions by REGION OVERLAP (caller-supplied
file_paths → pinned decisions) rather than solely by keyword match on decision
description text.

The core scenario: a decision is stored with description "High recall: no false
negatives on drift/grounding", pinned to some_module.py. The preflight topic is
"improve retrieval quality for the locator" — zero keyword overlap with the
description, so ledger keyword search returns nothing. The caller passes
file_paths=["some_module.py"]; the region-anchored arm looks up the pinned
decision and surfaces it.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts import (
    DecisionMatch,
    LinkCommitResponse,
    SearchDecisionsResponse,
)
from handlers.preflight import (
    _merge_decision_matches,
    _region_anchored_preflight,
    handle_preflight,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_link_commit_response():
    return LinkCommitResponse(
        commit_hash="abc123",
        synced=True,
        reason="already_synced",
    )


def _make_region_decision(
    decision_id: str = "decision:r1",
    description: str = "High recall: no false negatives on drift/grounding",
    status: str = "reflected",
    file_path: str = "pilot/mcp/some_module.py",
    symbol: str = "SomeSymbol",
) -> dict:
    """Raw dict as returned by get_decisions_for_files."""
    return {
        "decision_id": decision_id,
        "description": description,
        "source_type": "transcript",
        "source_ref": "meeting-2026-04-21",
        "source_excerpt": "",
        "meeting_date": "",
        "ingested_at": "2026-04-21",
        "status": status,
        "signoff": None,
        "code_region": {
            "file_path": file_path,
            "symbol": symbol,
            "lines": (52, 99),
            "purpose": description,
            "content_hash": "abc",
        },
    }


def _make_ctx(
    region_decisions: list[dict] | None = None,
    keyword_matches: list[DecisionMatch] | None = None,
    guided_mode: bool = True,
) -> SimpleNamespace:
    """Build a minimal fake BicameralContext.

    No code_locator is required in the new flow — the caller passes file_paths
    directly. The ledger returns region_decisions for whatever paths were
    queried.
    """
    ledger = MagicMock()
    ledger.ingest_commit = AsyncMock(return_value={
        "commit_hash": "abc123",
        "new_decisions_linked": 0,
        "drift_detected": [],
        "symbols_indexed": 0,
    })
    ledger.get_decisions_for_files = AsyncMock(return_value=region_decisions or [])
    ledger.search_by_query = AsyncMock(return_value=[])

    matches = keyword_matches or []
    search_resp = SearchDecisionsResponse(
        query="",
        sync_status=_make_link_commit_response(),
        matches=matches,
        ungrounded_count=0,
        suggested_review=[],
    )
    search_resp.action_hints = []

    ctx = SimpleNamespace(
        repo_path=".",
        ledger=ledger,
        guided_mode=guided_mode,
        _sync_state={},
    )
    return ctx, search_resp


# ── Unit: _region_anchored_preflight ────────────────────────────────────────


@pytest.mark.asyncio
async def test_region_anchored_returns_pinned_decisions():
    """Caller-supplied file_path → ledger returns a pinned decision."""
    ctx, _ = _make_ctx(region_decisions=[_make_region_decision()])

    matches = await _region_anchored_preflight(ctx, ["pilot/mcp/some_module.py"])

    assert len(matches) == 1
    assert matches[0].decision_id == "decision:r1"
    assert matches[0].confidence == 0.9
    assert matches[0].code_regions[0].file_path == "pilot/mcp/some_module.py"


@pytest.mark.asyncio
async def test_region_anchored_deduplicates_same_decision_across_files():
    """Same decision pinned to two files → appears only once."""
    ctx, _ = _make_ctx(
        region_decisions=[
            _make_region_decision(decision_id="decision:d1", file_path="file_a.py"),
            _make_region_decision(decision_id="decision:d1", file_path="file_b.py"),
        ],
    )

    matches = await _region_anchored_preflight(ctx, ["file_a.py", "file_b.py"])
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_region_anchored_returns_empty_when_file_paths_empty():
    """Empty or missing file_paths → graceful empty result."""
    ctx, _ = _make_ctx()

    assert await _region_anchored_preflight(ctx, []) == []
    assert await _region_anchored_preflight(ctx, [""]) == []
    assert await _region_anchored_preflight(ctx, ["  "]) == []


@pytest.mark.asyncio
async def test_region_anchored_dedups_input_paths():
    """Duplicate paths from caller → ledger called with deduped list."""
    ctx, _ = _make_ctx(region_decisions=[])

    await _region_anchored_preflight(ctx, ["a.py", "b.py", "a.py"])

    called_paths = ctx.ledger.get_decisions_for_files.call_args[0][0]
    assert called_paths == ["a.py", "b.py"]


@pytest.mark.asyncio
async def test_region_anchored_returns_empty_when_ledger_raises():
    """Ledger error → fail-open, empty result."""
    ctx, _ = _make_ctx()
    ctx.ledger.get_decisions_for_files = AsyncMock(side_effect=RuntimeError("db down"))

    matches = await _region_anchored_preflight(ctx, ["some_file.py"])
    assert matches == []


# ── Unit: _merge_decision_matches ───────────────────────────────────────────


def _dm(decision_id: str, status: str = "reflected") -> DecisionMatch:
    return DecisionMatch(
        decision_id=decision_id,
        description="test",
        status=status,
        confidence=0.8,
        source_ref="",
        code_regions=[],
    )


def test_merge_region_first():
    """Region matches come before keyword matches in output."""
    region = [_dm("d:region")]
    keyword = [_dm("d:keyword")]
    merged = _merge_decision_matches(region, keyword)
    assert [m.decision_id for m in merged] == ["d:region", "d:keyword"]


def test_merge_deduplicates_by_decision_id():
    """Same decision_id in both → only region version kept (first seen)."""
    region = [_dm("d:shared")]
    keyword = [_dm("d:shared"), _dm("d:keywordonly")]
    merged = _merge_decision_matches(region, keyword)
    assert len(merged) == 2
    assert merged[0].decision_id == "d:shared"
    assert merged[1].decision_id == "d:keywordonly"


# ── Integration: handle_preflight fires on region hit with zero keyword overlap ─


def _make_raw_search_row(
    decision_id: str = "d:keyword",
    description: str = "test",
    status: str = "drifted",
) -> dict:
    """Raw row shape returned by ctx.ledger.search_by_query.

    Matches the format consumed by handlers/_match_shaping._raw_to_decision_match:
    flat top-level fields plus optional ``code_regions`` list.
    """
    return {
        "decision_id": decision_id,
        "description": description,
        "status": status,
        "confidence": 0.8,
        "source_ref": "",
        "code_regions": [],
        "drift_evidence": "",
        "related_constraints": [],
        "source_excerpt": "",
        "meeting_date": "",
        "signoff": None,
    }


@pytest.mark.asyncio
async def test_preflight_fires_on_region_hit_no_keyword():
    """Core regression: preflight surfaces a decision even when the ledger
    keyword search returns nothing because the topic has zero keyword overlap
    with the description.

    Region-anchored path: caller passes file_paths → pinned decisions.
    """
    ctx, _ = _make_ctx(
        region_decisions=[_make_region_decision(status="reflected")],
        keyword_matches=[],
        guided_mode=True,
    )

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_make_link_commit_response())):
        resp = await handle_preflight(
            ctx,
            topic="improve retrieval quality for the locator",
            file_paths=["pilot/mcp/some_module.py"],
        )

    assert resp.fired is True
    assert "region" in resp.sources_chained
    decision_ids = [d.decision_id for d in resp.decisions]
    assert "decision:r1" in decision_ids


@pytest.mark.asyncio
async def test_preflight_region_in_sources_chained():
    """sources_chained includes 'region' when caller passes file_paths and
    the ledger returns pinned decisions."""
    ctx, _ = _make_ctx(
        region_decisions=[_make_region_decision(status="drifted")],
        keyword_matches=[],
        guided_mode=False,  # normal mode — needs actionable signal
    )

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_make_link_commit_response())):
        resp = await handle_preflight(
            ctx,
            topic="improve something in the locator logic",
            file_paths=["some/file.py"],
        )

    assert "region" in resp.sources_chained


@pytest.mark.asyncio
async def test_preflight_topic_only_no_file_paths_still_works():
    """Caller omits file_paths → preflight uses ledger keyword search only.

    Regression: the v0.6.3 default path (topic only, no file_paths) must still
    surface keyword-matching decisions. With the F4 fix, preflight calls
    ctx.ledger.search_by_query directly (not handle_search_decisions, which
    would re-trigger handle_link_commit and double-sync).
    """
    ledger = MagicMock()
    ledger.ingest_commit = AsyncMock(return_value={
        "commit_hash": "abc123",
        "new_decisions_linked": 0,
        "drift_detected": [],
        "symbols_indexed": 0,
    })
    ledger.search_by_query = AsyncMock(return_value=[_make_raw_search_row(status="drifted")])

    ctx = SimpleNamespace(
        repo_path=".",
        ledger=ledger,
        guided_mode=False,
        _sync_state={},
    )

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_make_link_commit_response())):
        resp = await handle_preflight(ctx, topic="drifted stripe webhook handler")

    assert resp.fired is True
    assert "region" not in resp.sources_chained
    assert "keyword" in resp.sources_chained
