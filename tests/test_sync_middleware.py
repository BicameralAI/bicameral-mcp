"""Tests for sync_middleware — session-start banner and ledger catch-up (v0.6.1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.sync_middleware import ensure_ledger_synced, get_session_start_banner


def _make_ctx(open_rows=None, last_sync_sha=None, session_started=False):
    """Build a minimal ctx mock with a _sync_state dict and a ledger.

    ``open_rows`` is the list returned by ``ledger.get_decisions_by_status``.
    Each row should include a ``status`` key ("drifted" or "ungrounded") so
    the banner can count them correctly.
    """
    ctx = MagicMock()
    ctx.repo_path = str(Path(__file__).resolve().parents[1])
    ctx._sync_state = {"session_started": session_started}
    if last_sync_sha:
        ctx._sync_state["last_sync_sha"] = last_sync_sha

    ledger = AsyncMock()
    ledger.get_decisions_by_status = AsyncMock(return_value=open_rows or [])
    ctx.ledger = ledger
    return ctx


def _drifted(decision_id="decision:1", description="Auth uses JWT", source_ref="arch-review"):
    return {
        "decision_id": decision_id,
        "description": description,
        "source_ref": source_ref,
        "status": "drifted",
    }


def _ungrounded(decision_id="decision:2", description="Billing uses Stripe", source_ref="pm-doc"):
    return {
        "decision_id": decision_id,
        "description": description,
        "source_ref": source_ref,
        "status": "ungrounded",
    }


def _proposal(decision_id="decision:3", description="Rate limit is 100 req/s",
              source_ref="sprint-notes", days_old=15):
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    return {
        "decision_id": decision_id,
        "description": description,
        "source_ref": source_ref,
        "status": "ungrounded",  # code-compliance axis; "proposal" is gone post-decoupling
        "signoff": {"state": "proposed", "created_at": created_at},
    }


# ── get_session_start_banner ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_banner_none_when_no_open_decisions():
    ctx = _make_ctx(open_rows=[])
    banner = await get_session_start_banner(ctx)
    assert banner is None


@pytest.mark.asyncio
async def test_banner_returned_on_first_call_with_drifted():
    ctx = _make_ctx(open_rows=[_drifted()])
    banner = await get_session_start_banner(ctx)
    assert banner is not None
    assert banner.drifted_count == 1
    assert banner.ungrounded_count == 0
    assert banner.items[0]["decision_id"] == "decision:1"
    assert banner.items[0]["status"] == "drifted"
    assert "drifted" in banner.message


@pytest.mark.asyncio
async def test_banner_includes_ungrounded_decisions():
    """Ungrounded decisions are 'still floating' per Jacob's ask and must appear."""
    ctx = _make_ctx(open_rows=[_drifted(), _ungrounded()])
    banner = await get_session_start_banner(ctx)
    assert banner is not None
    assert banner.drifted_count == 1
    assert banner.ungrounded_count == 1
    assert len(banner.items) == 2
    statuses = sorted(item["status"] for item in banner.items)
    assert statuses == ["drifted", "ungrounded"]
    assert "drifted" in banner.message and "ungrounded" in banner.message


@pytest.mark.asyncio
async def test_banner_queries_both_drifted_and_ungrounded_statuses():
    ctx = _make_ctx(open_rows=[_drifted()])
    await get_session_start_banner(ctx)
    ctx.ledger.get_decisions_by_status.assert_called_once_with(["drifted", "ungrounded", "context_pending"])


@pytest.mark.asyncio
async def test_banner_truncates_at_10_items_with_drifted_prioritized():
    # 12 open items: 3 drifted + 9 ungrounded. Truncated view should keep
    # all 3 drifted first, then fill with ungrounded up to the 10-item cap.
    rows = [_drifted(decision_id=f"decision:d{i}") for i in range(3)] + \
           [_ungrounded(decision_id=f"decision:u{i}") for i in range(9)]
    ctx = _make_ctx(open_rows=rows)
    banner = await get_session_start_banner(ctx)
    assert banner is not None
    assert banner.drifted_count == 3        # full count, not truncated
    assert banner.ungrounded_count == 9
    assert len(banner.items) == 10          # list is capped
    assert banner.truncated is True
    # All 3 drifted must be present in the truncated view
    assert sum(1 for i in banner.items if i["status"] == "drifted") == 3
    assert f"top 10" in banner.message


@pytest.mark.asyncio
async def test_banner_not_truncated_when_under_cap():
    ctx = _make_ctx(open_rows=[_drifted(), _ungrounded()])
    banner = await get_session_start_banner(ctx)
    assert banner is not None
    assert banner.truncated is False
    assert "top" not in banner.message


@pytest.mark.asyncio
async def test_banner_only_fires_once_per_session():
    ctx = _make_ctx(open_rows=[_drifted()])
    first = await get_session_start_banner(ctx)
    second = await get_session_start_banner(ctx)
    assert first is not None
    assert second is None  # session_started=True after first call
    # DB queried exactly once
    ctx.ledger.get_decisions_by_status.assert_called_once()


@pytest.mark.asyncio
async def test_banner_none_when_already_started():
    ctx = _make_ctx(session_started=True, open_rows=[_drifted()])
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


# ── stale proposal banner (v0.7.0) ──────────────────────────────────


@pytest.mark.asyncio
async def test_banner_surfaces_stale_proposal():
    """Proposals idle >14 days appear in the banner with stale_proposal_count."""
    ctx = _make_ctx(open_rows=[_proposal(days_old=15)])
    banner = await get_session_start_banner(ctx)
    assert banner is not None
    assert banner.stale_proposal_count == 1
    assert banner.proposal_count == 1
    assert "stale proposal" in banner.message
    assert any(i.get("signoff_state") == "proposed" for i in banner.items)


@pytest.mark.asyncio
async def test_banner_silent_on_fresh_proposal():
    """Proposals <14 days old are expected noise — banner must not fire."""
    ctx = _make_ctx(open_rows=[_proposal(days_old=3)])
    banner = await get_session_start_banner(ctx)
    assert banner is None


# ── V1 A2-light: repo_write_barrier ─────────────────────────────────


@pytest.fixture
def _reset_locks():
    """Drop the per-repo lock registry before and after each test so lock
    identity is deterministic across tests in the same process."""
    from handlers.sync_middleware import _reset_repo_locks_for_tests
    _reset_repo_locks_for_tests()
    yield
    _reset_repo_locks_for_tests()


def _barrier_ctx(repo_path: str):
    ctx = MagicMock()
    ctx.repo_path = repo_path
    return ctx


@pytest.mark.asyncio
async def test_repo_write_barrier_serializes_same_repo(_reset_locks):
    """Two concurrent barrier-holders for the same repo MUST serialize.

    Proves the in-process race window V1 A2-light is closing: a second
    bind call cannot observe the ledger while the first is mid-write.
    """
    import asyncio
    from handlers.sync_middleware import repo_write_barrier

    events: list[str] = []

    async def task(name: str, hold_ms: int):
        ctx = _barrier_ctx("/repo/a")
        async with repo_write_barrier(ctx) as _t:
            events.append(f"{name}:enter")
            await asyncio.sleep(hold_ms / 1000)
            events.append(f"{name}:exit")

    await asyncio.gather(task("first", 50), task("second", 10))

    # First must fully exit before second enters — no interleaving.
    assert events == ["first:enter", "first:exit", "second:enter", "second:exit"], events


@pytest.mark.asyncio
async def test_repo_write_barrier_allows_different_repos_concurrently(_reset_locks):
    """Different repos use different locks and MUST run in parallel."""
    import asyncio
    from handlers.sync_middleware import repo_write_barrier

    events: list[str] = []

    async def task(name: str, repo: str):
        ctx = _barrier_ctx(repo)
        async with repo_write_barrier(ctx) as _t:
            events.append(f"{name}:enter")
            await asyncio.sleep(0.05)
            events.append(f"{name}:exit")

    await asyncio.gather(task("A", "/repo/a"), task("B", "/repo/b"))

    # Both entered before either exited — barriers on different repos
    # do not block each other.
    assert events[:2] == ["A:enter", "B:enter"] or events[:2] == ["B:enter", "A:enter"]
    assert set(events) == {"A:enter", "A:exit", "B:enter", "B:exit"}


@pytest.mark.asyncio
async def test_repo_write_barrier_releases_on_exception(_reset_locks):
    """If the body raises, the lock must still release so the next caller proceeds."""
    import asyncio
    from handlers.sync_middleware import repo_write_barrier

    ctx = _barrier_ctx("/repo/a")

    with pytest.raises(RuntimeError):
        async with repo_write_barrier(ctx) as _t:
            raise RuntimeError("boom")

    async def reacquire():
        async with repo_write_barrier(ctx) as _t:
            return "ok"

    result = await asyncio.wait_for(reacquire(), timeout=1.0)
    assert result == "ok"


@pytest.mark.asyncio
async def test_repo_write_barrier_falls_back_when_repo_path_missing(_reset_locks):
    """Missing ctx.repo_path falls back to a default key and still serializes."""
    import asyncio
    from handlers.sync_middleware import repo_write_barrier

    class _Bare:
        pass

    ctx = _Bare()

    events: list[str] = []

    async def task(name: str):
        async with repo_write_barrier(ctx) as _t:
            events.append(f"{name}:enter")
            await asyncio.sleep(0.03)
            events.append(f"{name}:exit")

    await asyncio.gather(task("x"), task("y"))

    assert events[0].endswith(":enter") and events[1].endswith(":exit")
    assert events[2].endswith(":enter") and events[3].endswith(":exit")


# ── V1 A3: barrier timing yield ─────────────────────────────────────


@pytest.mark.asyncio
async def test_repo_write_barrier_reports_held_ms(_reset_locks):
    """BarrierTiming.held_ms is populated on exit and is non-negative."""
    import asyncio
    from handlers.sync_middleware import repo_write_barrier

    ctx = _barrier_ctx("/repo/a")
    async with repo_write_barrier(ctx) as timing:
        assert timing.held_ms is None  # not yet populated
        await asyncio.sleep(0.02)
    assert timing.held_ms is not None
    assert timing.held_ms >= 20.0  # we slept 20ms, measured wall clock should reflect it
    assert timing.held_ms < 500.0  # and not be absurd


@pytest.mark.asyncio
async def test_repo_write_barrier_reports_held_ms_on_exception(_reset_locks):
    """held_ms is set even when the body raises."""
    from handlers.sync_middleware import repo_write_barrier

    ctx = _barrier_ctx("/repo/a")
    captured_timing = None

    with pytest.raises(RuntimeError):
        async with repo_write_barrier(ctx) as timing:
            captured_timing = timing
            raise RuntimeError("boom")

    assert captured_timing is not None
    assert captured_timing.held_ms is not None
    assert captured_timing.held_ms >= 0.0
