"""Tests for sync_middleware — session-start banner and ledger catch-up (v0.6.1)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.sync_middleware import ensure_ledger_synced, get_session_start_banner


def _make_ctx(drifted_rows=None, last_sync_sha=None, session_started=False):
    """Build a minimal ctx mock with a _sync_state dict and a ledger."""
    ctx = MagicMock()
    ctx.repo_path = str(Path(__file__).resolve().parents[1])
    ctx._sync_state = {"session_started": session_started}
    if last_sync_sha:
        ctx._sync_state["last_sync_sha"] = last_sync_sha

    ledger = AsyncMock()
    ledger.get_decisions_by_status = AsyncMock(return_value=drifted_rows or [])
    ctx.ledger = ledger
    return ctx


# ── get_session_start_banner ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_banner_none_when_no_drifted_decisions():
    ctx = _make_ctx(drifted_rows=[])
    banner = await get_session_start_banner(ctx)
    assert banner is None


@pytest.mark.asyncio
async def test_banner_returned_on_first_call_with_drifted():
    ctx = _make_ctx(drifted_rows=[
        {"decision_id": "decision:1", "description": "Auth uses JWT", "source_ref": "arch-review"},
    ])
    banner = await get_session_start_banner(ctx)
    assert banner is not None
    assert banner.drifted_count == 1
    assert banner.items[0]["decision_id"] == "decision:1"
    assert "drifted" in banner.message


@pytest.mark.asyncio
async def test_banner_only_fires_once_per_session():
    ctx = _make_ctx(drifted_rows=[
        {"decision_id": "decision:1", "description": "Auth uses JWT", "source_ref": ""},
    ])
    first = await get_session_start_banner(ctx)
    second = await get_session_start_banner(ctx)
    assert first is not None
    assert second is None  # session_started=True after first call
    # DB queried exactly once
    ctx.ledger.get_decisions_by_status.assert_called_once()


@pytest.mark.asyncio
async def test_banner_none_when_already_started():
    ctx = _make_ctx(session_started=True, drifted_rows=[
        {"decision_id": "decision:1", "description": "...", "source_ref": ""},
    ])
    banner = await get_session_start_banner(ctx)
    assert banner is None
    ctx.ledger.get_decisions_by_status.assert_not_called()


@pytest.mark.asyncio
async def test_banner_swallows_ledger_exception():
    ctx = _make_ctx()
    ctx.ledger.get_decisions_by_status = AsyncMock(side_effect=RuntimeError("db down"))
    banner = await get_session_start_banner(ctx)
    assert banner is None  # must not raise


@pytest.mark.asyncio
async def test_banner_none_when_sync_state_missing():
    ctx = MagicMock()
    ctx._sync_state = None
    banner = await get_session_start_banner(ctx)
    assert banner is None


# ── ensure_ledger_synced ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_calls_link_commit_when_head_advanced():
    ctx = _make_ctx(last_sync_sha="old_sha")

    with (
        patch("handlers.link_commit._read_current_head_sha", return_value="new_sha"),
        patch("handlers.link_commit.handle_link_commit", new_callable=AsyncMock) as mock_lc,
    ):
        await ensure_ledger_synced(ctx)
        mock_lc.assert_called_once_with(ctx, "HEAD")


@pytest.mark.asyncio
async def test_ensure_skips_link_commit_when_already_synced():
    ctx = _make_ctx(last_sync_sha="current_sha")

    with (
        patch("handlers.link_commit._read_current_head_sha", return_value="current_sha"),
        patch("handlers.link_commit.handle_link_commit", new_callable=AsyncMock) as mock_lc,
    ):
        await ensure_ledger_synced(ctx)
        mock_lc.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_swallows_link_commit_exception():
    ctx = _make_ctx()

    with patch("handlers.link_commit.handle_link_commit", new_callable=AsyncMock) as mock_lc:
        mock_lc.side_effect = RuntimeError("git not available")
        # Must not raise
        await ensure_ledger_synced(ctx)
