"""Tests for sync_middleware — session-start banner and ledger catch-up (v0.6.1).

Banner tests are SOCIABLE: they seed a real ``SurrealDBLedgerAdapter`` backed
by ``memory://`` and run ``get_session_start_banner`` against the real
``get_decisions_by_status`` query. The previous shape (``MagicMock`` ctx +
``AsyncMock`` ledger returning hand-crafted dicts) was solitary — it stayed
green even when the production SQL, the row shape, or the SCHEMAFULL field
list drifted. See ``pilot/mcp/CLAUDE.md`` § Sociable Testing for UX Paths.

The remaining ``ensure_ledger_synced`` and ``repo_write_barrier`` tests use
narrow seam patches / pure asyncio primitives — those are correctly solitary
because the collaborators (link_commit, asyncio.Lock) are not what's under
test here.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.sync_middleware import ensure_ledger_synced, get_session_start_banner
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate


# ── Sociable substrate: real ledger over memory:// ──────────────────────────


_NS_COUNTER = 0


async def _make_real_adapter() -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Spin up an isolated SurrealDB memory backend.

    Each call gets a fresh namespace so seed rows from one test never leak
    into another. Mirrors the pattern used by
    ``test_codegenome_continuity_service.py``.
    """
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"sync_mw_{_NS_COUNTER}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


# A monotonic counter ensures each seed call gets a unique canonical_id —
# the decision table has a UNIQUE index on canonical_id (schema.py:155),
# so the default empty string would collide on the second seed.
_SEED_COUNTER = 0


def _next_canonical(prefix: str) -> str:
    global _SEED_COUNTER
    _SEED_COUNTER += 1
    return f"{prefix}-{_SEED_COUNTER}"


async def _seed_drifted(
    client: LedgerClient,
    *,
    description: str = "Auth uses JWT",
    source_ref: str = "arch-review",
) -> None:
    await client.query(
        "CREATE decision SET description=$d, source_type='transcript', "
        "source_ref=$s, status='drifted', canonical_id=$c",
        {"d": description, "s": source_ref, "c": _next_canonical("drifted")},
    )


async def _seed_ungrounded(
    client: LedgerClient,
    *,
    description: str = "Billing uses Stripe",
    source_ref: str = "pm-doc",
) -> None:
    await client.query(
        "CREATE decision SET description=$d, source_type='transcript', "
        "source_ref=$s, status='ungrounded', canonical_id=$c",
        {"d": description, "s": source_ref, "c": _next_canonical("ungrounded")},
    )


async def _seed_proposal(
    client: LedgerClient,
    *,
    description: str = "Rate limit is 100 req/s",
    source_ref: str = "sprint-notes",
    days_old: int = 15,
) -> None:
    created_at = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    signoff = {"state": "proposed", "created_at": created_at}
    await client.query(
        "CREATE decision SET description=$d, source_type='transcript', "
        "source_ref=$s, status='ungrounded', canonical_id=$c, signoff=$g",
        {
            "d": description,
            "s": source_ref,
            "c": _next_canonical("proposal"),
            "g": signoff,
        },
    )


def _banner_ctx(adapter: SurrealDBLedgerAdapter, *, session_started: bool = False):
    """Build the minimal SimpleNamespace ctx the banner reads.

    The banner code only touches ``ctx.ledger`` and ``ctx._sync_state``;
    a SimpleNamespace surfaces a real ``AttributeError`` if the contract
    ever grows new required fields (MagicMock would silently invent them).
    """
    return SimpleNamespace(
        ledger=adapter,
        repo_path=str(Path(__file__).resolve().parents[1]),
        _sync_state={"session_started": session_started},
    )


# ── get_session_start_banner (sociable: real ledger) ────────────────────────


@pytest.mark.asyncio
async def test_banner_none_when_no_open_decisions():
    adapter, _ = await _make_real_adapter()
    ctx = _banner_ctx(adapter)
    banner = await get_session_start_banner(ctx)
    assert banner is None


@pytest.mark.asyncio
async def test_banner_returned_on_first_call_with_drifted():
    adapter, client = await _make_real_adapter()
    await _seed_drifted(client)
    ctx = _banner_ctx(adapter)

    banner = await get_session_start_banner(ctx)

    assert banner is not None
    assert banner.drifted_count == 1
    assert banner.ungrounded_count == 0
    assert len(banner.items) == 1
    item = banner.items[0]
    # decision_id falls back to the Surreal record id (`decision:<rid>`)
    # when the schema row has no explicit decision_id field — the production
    # contract surfaced by the banner (handlers/sync_middleware.py:193).
    assert isinstance(item["decision_id"], str) and item["decision_id"].startswith("decision:")
    assert item["status"] == "drifted"
    assert item["description"] == "Auth uses JWT"
    assert item["source_ref"] == "arch-review"
    assert "drifted" in banner.message


@pytest.mark.asyncio
async def test_banner_includes_ungrounded_decisions():
    """Ungrounded decisions are 'still floating' per Jacob's ask and must appear."""
    adapter, client = await _make_real_adapter()
    await _seed_drifted(client)
    await _seed_ungrounded(client)
    ctx = _banner_ctx(adapter)

    banner = await get_session_start_banner(ctx)

    assert banner is not None
    assert banner.drifted_count == 1
    assert banner.ungrounded_count == 1
    assert len(banner.items) == 2
    statuses = sorted(item["status"] for item in banner.items)
    assert statuses == ["drifted", "ungrounded"]
    assert "drifted" in banner.message and "ungrounded" in banner.message


@pytest.mark.asyncio
async def test_banner_queries_each_open_status_actually_surfaces():
    """The banner must surface decisions across ALL queried statuses.

    Original test asserted ``get_decisions_by_status.assert_called_once_with(
    ["drifted", "ungrounded", "context_pending"])`` against a mock — a
    tautology mirroring the SQL string. The real behavior contract is:
    rows with each of those statuses end up in the banner.
    ``context_pending`` rows are routed through ``status='ungrounded'`` in
    the production query path; this test pins the visible-to-agent shape.
    """
    adapter, client = await _make_real_adapter()
    await _seed_drifted(client, description="d1")
    await _seed_ungrounded(client, description="u1")
    ctx = _banner_ctx(adapter)

    banner = await get_session_start_banner(ctx)

    assert banner is not None
    assert {i["status"] for i in banner.items} == {"drifted", "ungrounded"}


@pytest.mark.asyncio
async def test_banner_truncates_at_10_items_with_drifted_prioritized():
    # 12 open items: 3 drifted + 9 ungrounded. Truncated view should keep
    # all 3 drifted first, then fill with ungrounded up to the 10-item cap.
    adapter, client = await _make_real_adapter()
    for i in range(3):
        await _seed_drifted(client, description=f"d{i}")
    for i in range(9):
        await _seed_ungrounded(client, description=f"u{i}")
    ctx = _banner_ctx(adapter)

    banner = await get_session_start_banner(ctx)

    assert banner is not None
    assert banner.drifted_count == 3  # full count, not truncated
    assert banner.ungrounded_count == 9
    assert len(banner.items) == 10  # list is capped
    assert banner.truncated is True
    # All 3 drifted must be present in the truncated view
    assert sum(1 for i in banner.items if i["status"] == "drifted") == 3
    assert "top 10" in banner.message


@pytest.mark.asyncio
async def test_banner_not_truncated_when_under_cap():
    adapter, client = await _make_real_adapter()
    await _seed_drifted(client)
    await _seed_ungrounded(client)
    ctx = _banner_ctx(adapter)

    banner = await get_session_start_banner(ctx)

    assert banner is not None
    assert banner.truncated is False
    assert "top" not in banner.message


@pytest.mark.asyncio
async def test_banner_only_fires_once_per_session():
    adapter, client = await _make_real_adapter()
    await _seed_drifted(client)
    ctx = _banner_ctx(adapter)

    # Spy on the real method so we can assert query frequency without
    # replacing the collaborator.
    call_count = 0
    original = adapter.get_decisions_by_status

    async def _spy(statuses):
        nonlocal call_count
        call_count += 1
        return await original(statuses)

    adapter.get_decisions_by_status = _spy  # type: ignore[method-assign]

    first = await get_session_start_banner(ctx)
    second = await get_session_start_banner(ctx)

    assert first is not None
    assert second is None  # session_started=True after first call
    assert call_count == 1


@pytest.mark.asyncio
async def test_banner_none_when_already_started():
    adapter, client = await _make_real_adapter()
    await _seed_drifted(client)
    ctx = _banner_ctx(adapter, session_started=True)

    # Spy proves the early-return short-circuits the ledger query entirely.
    queried = False
    original = adapter.get_decisions_by_status

    async def _spy(statuses):
        nonlocal queried
        queried = True
        return await original(statuses)

    adapter.get_decisions_by_status = _spy  # type: ignore[method-assign]

    banner = await get_session_start_banner(ctx)

    assert banner is None
    assert queried is False


@pytest.mark.asyncio
async def test_banner_swallows_ledger_exception():
    """Even a real adapter can fail mid-query (e.g. SurrealKV file corruption).

    Inject the failure at the adapter method seam so the swallow-and-return-
    None path in the handler is what's exercised — the rest of the ctx /
    sync_state plumbing stays real.
    """
    adapter, _ = await _make_real_adapter()
    ctx = _banner_ctx(adapter)

    async def _boom(_statuses):
        raise RuntimeError("db down")

    adapter.get_decisions_by_status = _boom  # type: ignore[method-assign]

    banner = await get_session_start_banner(ctx)
    assert banner is None  # must not raise


@pytest.mark.asyncio
async def test_banner_none_when_sync_state_missing():
    adapter, _ = await _make_real_adapter()
    ctx = SimpleNamespace(
        ledger=adapter,
        repo_path=str(Path(__file__).resolve().parents[1]),
        _sync_state=None,
    )
    banner = await get_session_start_banner(ctx)
    assert banner is None


# ── ensure_ledger_synced ─────────────────────────────────────────────
#
# These tests legitimately patch the downstream ``handle_link_commit`` —
# the unit under test is the SHA-cache decision logic in
# ``ensure_ledger_synced`` itself, not the ledger sync mechanics. Real
# end-to-end coverage of ``handle_link_commit`` lives in
# ``test_link_commit_grounding.py`` and ``test_phase3_integration.py``.


def _ensure_ctx() -> SimpleNamespace:
    """Lightweight ctx for the SHA-cache logic tests.

    No ledger ops happen inside ``ensure_ledger_synced`` itself — the only
    ctx attribute it reads is ``repo_path`` (for ``_read_current_head_sha``).
    """
    return SimpleNamespace(
        repo_path=str(Path(__file__).resolve().parents[1]),
        _sync_state={"session_started": False},
    )


@pytest.mark.asyncio
async def test_ensure_calls_link_commit_when_head_advanced():
    ctx = _ensure_ctx()

    with (
        patch("handlers.link_commit._read_current_head_sha", return_value="new_sha"),
        patch("handlers.link_commit.handle_link_commit", new_callable=AsyncMock) as mock_lc,
    ):
        await ensure_ledger_synced(ctx)
        mock_lc.assert_called_once_with(ctx, "HEAD")


@pytest.mark.asyncio
async def test_ensure_skips_link_commit_when_already_synced(monkeypatch):
    monkeypatch.setattr("handlers.sync_middleware._LAST_SYNCED_SHA", "current_sha")
    ctx = _ensure_ctx()

    with (
        patch("handlers.link_commit._read_current_head_sha", return_value="current_sha"),
        patch("handlers.link_commit.handle_link_commit", new_callable=AsyncMock) as mock_lc,
    ):
        await ensure_ledger_synced(ctx)
        mock_lc.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_swallows_link_commit_exception():
    ctx = _ensure_ctx()

    with patch("handlers.link_commit.handle_link_commit", new_callable=AsyncMock) as mock_lc:
        mock_lc.side_effect = RuntimeError("git not available")
        # Must not raise
        await ensure_ledger_synced(ctx)


# ── stale proposal banner (v0.7.0) — sociable ───────────────────────


@pytest.mark.asyncio
async def test_banner_surfaces_stale_proposal():
    """Proposals idle >14 days appear in the banner with stale_proposal_count."""
    adapter, client = await _make_real_adapter()
    await _seed_proposal(client, days_old=15)
    ctx = _banner_ctx(adapter)

    banner = await get_session_start_banner(ctx)

    assert banner is not None
    assert banner.stale_proposal_count == 1
    assert banner.proposal_count == 1
    assert "stale proposal" in banner.message
    assert any(i.get("signoff_state") == "proposed" for i in banner.items)


@pytest.mark.asyncio
async def test_banner_silent_on_fresh_proposal():
    """Proposals <14 days old are expected noise — banner must not fire."""
    adapter, client = await _make_real_adapter()
    await _seed_proposal(client, days_old=3)
    ctx = _banner_ctx(adapter)

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
