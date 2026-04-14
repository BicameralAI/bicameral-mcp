"""bicameral_reset regression tests (v0.4.6).

Four cases cover the safety-critical properties of the fail-safe valve:
  1. Dry run returns the plan without wiping
  2. Confirm=True actually wipes the scoped tables
  3. Multi-repo isolation — wiping repo A leaves repo B's rows intact
  4. Replay plan lists every source_cursor that existed before the wipe

The tests use a memory-backed SurrealDB so nothing touches disk.
"""

from __future__ import annotations

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.reset import handle_reset


# ── Helpers ─────────────────────────────────────────────────────────


def _payload_for(repo: str, source_type: str, source_ref: str) -> dict:
    return {
        "query": f"test ingest for {repo}",
        "repo": repo,
        "analyzed_at": "2026-04-14T00:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": f"{source_type}-{source_ref}",
                    "source_type": source_type,
                    "text": f"decision from {source_ref}",
                    "source_ref": source_ref,
                },
                "intent": f"decision from {source_ref}",
                "symbols": [f"Symbol_{source_ref}"],
                "code_regions": [
                    {
                        "file_path": f"fake/{source_ref}.py",
                        "symbol": f"Symbol_{source_ref}",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 10,
                        "purpose": "test",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }


async def _seed_repo_with_cursors(
    ledger, repo: str, count: int = 3, source_type: str = "slack",
) -> None:
    """Seed N source_cursor rows for a repo by upserting them directly."""
    for i in range(count):
        await ledger.ingest_payload(_payload_for(repo, source_type, f"msg_{i}"))
        await ledger.upsert_source_cursor(
            repo=repo,
            source_type=source_type,
            source_scope=f"scope_{i}",
            cursor=f"cursor_{i}",
            last_source_ref=f"msg_{i}",
        )


def _ctx(repo_path: str = "test-repo") -> BicameralContext:
    """Minimal ctx with ledger + repo_path. code_graph / drift_analyzer
    are left as whatever from_env builds — reset doesn't use them.
    """
    import os
    os.environ["REPO_PATH"] = repo_path
    return BicameralContext.from_env()


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_reset_dry_run_returns_plan_without_wiping(monkeypatch, surreal_url):
    """confirm=False must NOT touch the ledger and must return the cursors."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()

    ledger = get_ledger()
    await ledger.connect()
    await _seed_repo_with_cursors(ledger, repo="test-repo-A", count=3)

    ctx = _ctx(repo_path="test-repo-A")

    result = await handle_reset(ctx, confirm=False)

    assert result.wiped is False
    assert result.cursors_before == 3
    assert result.repo == "test-repo-A"
    assert len(result.replay_plan) == 3
    # Cursors still exist in the ledger
    remaining = await ledger.get_all_source_cursors("test-repo-A")
    assert len(remaining) == 3, "Dry run must not touch the ledger"

    reset_ledger_singleton()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_reset_confirm_actually_wipes(monkeypatch, surreal_url):
    """confirm=True must wipe cursors AND intents/regions scoped to the repo."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()

    ledger = get_ledger()
    await ledger.connect()
    await _seed_repo_with_cursors(ledger, repo="test-repo-A", count=4)

    # Sanity: decisions exist before wipe
    pre_decisions = await ledger.get_all_decisions()
    assert len(pre_decisions) >= 4, f"precondition failed: {len(pre_decisions)} decisions seeded"

    ctx = _ctx(repo_path="test-repo-A")
    result = await handle_reset(ctx, confirm=True)

    assert result.wiped is True
    assert result.cursors_before == 4
    assert len(result.replay_plan) == 4

    post_cursors = await ledger.get_all_source_cursors("test-repo-A")
    assert post_cursors == [], f"wipe did not clear source_cursor: {post_cursors}"

    post_decisions = await ledger.get_all_decisions()
    # Any leftover rows should not be scoped to test-repo-A. The ledger
    # may have leftover edge/symbol rows from other tests, but intent
    # rows for this repo should be gone.
    for d in post_decisions:
        # description-based check — the seeded decisions had distinctive
        # 'decision from msg_N' descriptions
        assert "decision from msg_" not in d.get("description", ""), (
            f"wipe missed an intent: {d}"
        )

    reset_ledger_singleton()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_reset_multi_repo_isolation(monkeypatch, surreal_url):
    """Wiping repo A must NOT affect repo B's rows in the same SurrealDB."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()

    ledger = get_ledger()
    await ledger.connect()

    await _seed_repo_with_cursors(ledger, repo="repo-A", count=2, source_type="slack")
    await _seed_repo_with_cursors(ledger, repo="repo-B", count=3, source_type="notion")

    pre_a = await ledger.get_all_source_cursors("repo-A")
    pre_b = await ledger.get_all_source_cursors("repo-B")
    assert len(pre_a) == 2
    assert len(pre_b) == 3

    ctx = _ctx(repo_path="repo-A")
    result = await handle_reset(ctx, confirm=True)
    assert result.wiped is True

    post_a = await ledger.get_all_source_cursors("repo-A")
    post_b = await ledger.get_all_source_cursors("repo-B")

    assert post_a == [], f"repo-A wipe left cursors: {post_a}"
    assert len(post_b) == 3, (
        f"repo-A wipe leaked into repo-B: {len(post_b)} cursors remain "
        f"(expected 3). THIS IS A SAFETY REGRESSION — multi-repo isolation "
        f"is a hard guarantee of the reset tool."
    )

    reset_ledger_singleton()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_reset_replay_plan_preserves_source_refs(monkeypatch, surreal_url):
    """The replay plan must carry source_type + source_scope + last_source_ref
    for every wiped cursor, so the caller can re-ingest.
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()

    ledger = get_ledger()
    await ledger.connect()
    await _seed_repo_with_cursors(ledger, repo="repo-X", count=3, source_type="slack")

    ctx = _ctx(repo_path="repo-X")
    result = await handle_reset(ctx, confirm=False)

    assert len(result.replay_plan) == 3
    source_refs = sorted(e.last_source_ref for e in result.replay_plan)
    assert source_refs == ["msg_0", "msg_1", "msg_2"]
    scopes = sorted(e.source_scope for e in result.replay_plan)
    assert scopes == ["scope_0", "scope_1", "scope_2"]
    for entry in result.replay_plan:
        assert entry.source_type == "slack"

    reset_ledger_singleton()
