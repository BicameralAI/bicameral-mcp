"""Integration tests for handle_preflight's keyword-merge wiring.

The F4 contract: handle_preflight calls ctx.ledger.search_by_query DIRECTLY,
NOT via handle_search_decisions. The latter would trigger handle_link_commit
inside it, double-syncing every preflight call.

These tests lock the no-cascade behavior and the status-aware fired gating
specified in handle_preflight's docstring step 8.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contracts import LinkCommitResponse
from handlers.preflight import handle_preflight


def _make_ctx_with_search(raw_rows: list[dict] | None = None) -> SimpleNamespace:
    ledger = MagicMock()
    ledger.ingest_commit = AsyncMock(return_value={
        "commit_hash": "abc",
        "new_decisions_linked": 0,
        "drift_detected": [],
        "symbols_indexed": 0,
    })
    ledger.search_by_query = AsyncMock(return_value=raw_rows or [])
    ledger.get_decisions_for_files = AsyncMock(return_value=[])
    return SimpleNamespace(
        repo_path=".",
        ledger=ledger,
        guided_mode=False,
        _sync_state={},
    )


def _raw(decision_id: str, status: str = "drifted") -> dict:
    return {
        "decision_id": decision_id,
        "description": "kw",
        "status": status,
        "confidence": 0.7,
        "code_regions": [],
    }


def _link_commit_response() -> LinkCommitResponse:
    return LinkCommitResponse(commit_hash="abc", synced=True, reason="already_synced")


@pytest.mark.asyncio
async def test_handle_preflight_calls_search_by_query_not_handler() -> None:
    """The F4 contract: search_by_query is called once; handle_search_decisions
    is NEVER called from preflight (avoiding the double-sync cascade)."""
    ctx = _make_ctx_with_search([])

    with (
        patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())) as mock_link,
        patch("handlers.search_decisions.handle_search_decisions", new=AsyncMock()) as mock_handler,
    ):
        await handle_preflight(ctx, topic="some topic")

    # search_by_query was the path used — exactly once with the topic.
    ctx.ledger.search_by_query.assert_called_once()
    call_kwargs = ctx.ledger.search_by_query.call_args.kwargs
    assert call_kwargs.get("query") == "some topic"
    # handle_search_decisions was NOT called from preflight.
    mock_handler.assert_not_called()


@pytest.mark.asyncio
async def test_handle_preflight_does_not_trigger_link_commit() -> None:
    """handle_link_commit must not fire from inside preflight's keyword path.

    ensure_ledger_synced (called explicitly by handle_preflight) is the only
    sync. If handle_search_decisions were invoked, link_commit would fire
    again — that's the regression this test guards against.
    """
    ctx = _make_ctx_with_search([_raw("d:1", status="reflected")])

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())) as mock_link:
        await handle_preflight(ctx, topic="topic")

    # ensure_ledger_synced may call handle_link_commit at most once.
    # The keyword path must not trigger an additional call.
    assert mock_link.call_count <= 1


@pytest.mark.asyncio
async def test_fired_true_when_keyword_match_drifted_normal_mode() -> None:
    """Status-aware gating: keyword hit with drifted status fires preflight
    in normal (non-guided) mode. This is the broadening the merge enables."""
    ctx = _make_ctx_with_search([_raw("d:kw", status="drifted")])
    ctx.guided_mode = False

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())):
        resp = await handle_preflight(ctx, topic="t")

    assert resp.fired is True


@pytest.mark.asyncio
async def test_fired_false_when_keyword_match_reflected_normal_mode() -> None:
    """Reflected matches alone do NOT fire preflight in normal mode."""
    ctx = _make_ctx_with_search([_raw("d:kw", status="reflected")])
    ctx.guided_mode = False

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())):
        resp = await handle_preflight(ctx, topic="t")

    assert resp.fired is False


@pytest.mark.asyncio
async def test_fired_true_when_keyword_match_any_status_guided_mode() -> None:
    """Guided mode fires on ANY merged matches, regardless of status."""
    ctx = _make_ctx_with_search([_raw("d:kw", status="reflected")])
    ctx.guided_mode = True

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())):
        resp = await handle_preflight(ctx, topic="t")

    assert resp.fired is True


@pytest.mark.asyncio
async def test_decisions_response_includes_merged_keyword_match() -> None:
    """When only keyword matches present, response.decisions contains them."""
    ctx = _make_ctx_with_search([_raw("d:kw", status="ungrounded")])

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())):
        resp = await handle_preflight(ctx, topic="t")

    decision_ids = [d.decision_id for d in resp.decisions]
    assert "d:kw" in decision_ids


@pytest.mark.asyncio
async def test_keyword_lookup_failure_swallowed_gracefully() -> None:
    """If search_by_query raises, preflight continues (region-only path)."""
    ctx = _make_ctx_with_search()
    ctx.ledger.search_by_query = AsyncMock(side_effect=RuntimeError("db down"))

    with patch("handlers.link_commit.handle_link_commit", new=AsyncMock(return_value=_link_commit_response())):
        resp = await handle_preflight(ctx, topic="t")

    # Should not raise; fired=False because nothing else fired.
    assert resp.fired is False
