"""v0.4.8 — within-call sync dedup guard regression tests.

The dedup guard lives in ``handlers/link_commit.py``:
``_sync_cache_lookup`` / ``_store_sync_cache`` / ``invalidate_sync_cache``.
It short-circuits back-to-back ``handle_link_commit("HEAD")`` calls
within the same MCP invocation so auto-chains like ``ingest → brief``
don't do N× backfill + drift sweeps on the same unchanged HEAD.

These tests lock in the contract:

  1. Second call on unchanged HEAD returns ``reason="already_synced"``
     with the **same** ``regions_updated`` / ``decisions_drifted`` /
     ``decisions_reflected`` numbers as the first real call (B23:
     cached response, not synthetic zeros).
  2. After ``invalidate_sync_cache(ctx)``, the next call re-runs
     fresh — reason is NOT ``"already_synced"``.
  3. When HEAD advances between calls (real commit), the dedup
     skips the cache and re-runs a fresh sweep — proving the
     live ``git rev-parse HEAD`` check does its job.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.link_commit import (
    handle_link_commit,
    invalidate_sync_cache,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _seed_repo(repo_root: Path, body: str) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@e.com")
    _git(repo_root, "config", "user.name", "t")
    (repo_root / "pricing.py").write_text(dedent(body).strip() + "\n")
    _git(repo_root, "add", ".")
    _git(
        repo_root,
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "seed",
    )


def _commit_edit(repo_root: Path, new_body: str, message: str) -> None:
    (repo_root / "pricing.py").write_text(dedent(new_body).strip() + "\n")
    _git(repo_root, "add", "pricing.py")
    _git(
        repo_root,
        "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", message,
    )


@pytest.fixture
def _isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "repo"
    _seed_repo(
        repo_root,
        """
        def calculate_discount(order_total):
            if order_total >= 100:
                return order_total * 0.10
            return 0
        """,
    )
    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    yield repo_root
    reset_ledger_singleton()


def _ctx() -> BicameralContext:
    return BicameralContext.from_env()


# ── Contract tests ──────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_dedup_second_call_normalizes_reason(_isolated_ledger):
    """Second link_commit(HEAD) on unchanged HEAD must report
    ``reason == 'already_synced'`` so callers can distinguish a dedup
    hit from a fresh sync.
    """
    ledger = get_ledger()
    await ledger.connect()

    ctx = _ctx()
    r1 = await handle_link_commit(ctx, "HEAD")
    r2 = await handle_link_commit(ctx, "HEAD")

    assert r2.reason == "already_synced", (
        f"Dedup hit must normalize reason to 'already_synced', "
        f"got {r2.reason!r}"
    )
    # Cached fields should match the first call's real values (B23).
    assert r2.commit_hash == r1.commit_hash
    assert r2.regions_updated == r1.regions_updated
    assert r2.decisions_reflected == r1.decisions_reflected
    assert r2.decisions_drifted == r1.decisions_drifted


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_invalidate_forces_fresh_sync(_isolated_ledger, monkeypatch):
    """After ``invalidate_sync_cache(ctx)``, the next link_commit must
    actually call the ledger's ``ingest_commit`` again instead of
    short-circuiting. We prove the dedup was cleared by counting real
    ``ingest_commit`` invocations — not by the ``reason`` field, which
    the ledger's own sync_state cursor also reports as "already_synced"
    when the SHA hasn't changed (orthogonal layer).
    """
    ledger = get_ledger()
    await ledger.connect()

    # Spy on ingest_commit so we can count real calls.
    original_ingest_commit = ledger.ingest_commit
    call_count = {"n": 0}

    async def _counting_ingest_commit(*args, **kwargs):
        call_count["n"] += 1
        return await original_ingest_commit(*args, **kwargs)

    monkeypatch.setattr(ledger, "ingest_commit", _counting_ingest_commit)

    ctx = _ctx()
    await handle_link_commit(ctx, "HEAD")
    assert call_count["n"] == 1, (
        f"First call should hit the ledger once, got {call_count['n']}"
    )

    # Second call WITHOUT invalidate — dedup short-circuits, no ledger hit.
    await handle_link_commit(ctx, "HEAD")
    assert call_count["n"] == 1, (
        f"Second call without invalidate must dedup — ledger should NOT "
        f"have been re-hit. ingest_commit call count = {call_count['n']}"
    )

    # Third call AFTER invalidate — must re-hit the ledger.
    invalidate_sync_cache(ctx)
    await handle_link_commit(ctx, "HEAD")
    assert call_count["n"] == 2, (
        f"After invalidate_sync_cache, next link_commit must re-run "
        f"ingest_commit. Got call count = {call_count['n']} (expected 2)"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_head_advance_bypasses_dedup(_isolated_ledger):
    """If HEAD moves between calls, the dedup guard must detect the
    new SHA via live ``git rev-parse`` and run a fresh sync instead
    of returning the stale cache.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    ctx = _ctx()
    r1 = await handle_link_commit(ctx, "HEAD")

    # Advance HEAD with a real git commit.
    _commit_edit(
        repo_root,
        """
        def calculate_discount(order_total):
            # Totally different implementation.
            return 0
        """,
        "rewrite pricing",
    )

    r2 = await handle_link_commit(ctx, "HEAD")

    assert r2.reason != "already_synced", (
        f"HEAD advanced; dedup must NOT fire. Got reason={r2.reason!r}. "
        f"This means ctx.head_sha is stale and _sync_cache_lookup is "
        f"trusting it instead of re-reading git HEAD."
    )
    assert r2.commit_hash != r1.commit_hash, (
        f"New HEAD SHA should differ from old. r1={r1.commit_hash!r}, "
        f"r2={r2.commit_hash!r}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_explicit_sha_dedup(_isolated_ledger):
    """Two back-to-back link_commit calls with the same explicit SHA
    must dedup the second one too.
    """
    ledger = get_ledger()
    await ledger.connect()

    ctx = _ctx()
    # Use the repo's current HEAD SHA explicitly (not "HEAD" string).
    head_sha = _git(_isolated_ledger, "rev-parse", "HEAD")

    r1 = await handle_link_commit(ctx, head_sha)
    r2 = await handle_link_commit(ctx, head_sha)

    assert r2.reason == "already_synced", (
        f"Second call with same explicit SHA should dedup — "
        f"got reason={r2.reason!r}"
    )
    assert r2.commit_hash == r1.commit_hash
