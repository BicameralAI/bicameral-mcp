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
async def test_reset_wipes_entire_db(monkeypatch, surreal_url):
    """confirm=True must wipe ALL data — not just the repo passed in.

    Each DB instance is single-repo; the reset contract is a complete wipe
    followed by a re-init of the schema (ready for fresh ingestion).
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", surreal_url)
    reset_ledger_singleton()

    ledger = get_ledger()
    await ledger.connect()

    await _seed_repo_with_cursors(ledger, repo="repo-A", count=2, source_type="slack")
    await _seed_repo_with_cursors(ledger, repo="repo-B", count=3, source_type="notion")

    ctx = _ctx(repo_path="repo-A")
    result = await handle_reset(ctx, confirm=True)
    assert result.wiped is True

    post_cursors = await ledger.get_all_source_cursors("repo-A")
    assert post_cursors == [], f"wipe left cursors for repo-A: {post_cursors}"

    post_decisions = await ledger.get_all_decisions()
    assert post_decisions == [], f"wipe left decisions in DB: {post_decisions}"

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


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_reset_full_wipe_deletes_bicameral_dir(monkeypatch, tmp_path):
    """wipe_mode='full' must delete the entire .bicameral/ directory and
    leave the server in a usable state (schema reinitialised).
    """
    from adapters.ledger import reset_ledger_singleton as _reset

    bicameral_dir = tmp_path / ".bicameral"
    bicameral_dir.mkdir()
    (bicameral_dir / "config.yaml").write_text("guided: false\n")
    events_dir = bicameral_dir / "events"
    events_dir.mkdir()
    (events_dir / "test.jsonl").write_text('{"event":"test"}\n')

    ledger_path = str(bicameral_dir / "ledger.db")
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", f"surrealkv://{ledger_path}")
    _reset()

    ledger = get_ledger()
    await ledger.connect()
    await _seed_repo_with_cursors(ledger, repo="repo-full", count=2, source_type="notion")

    ctx = _ctx(repo_path="repo-full")

    # Dry run must surface the directory in the warning without deleting.
    dry = await handle_reset(ctx, confirm=False, wipe_mode="full")
    assert dry.wiped is False
    assert dry.wipe_mode == "full"
    assert str(bicameral_dir) in dry.bicameral_dir
    assert bicameral_dir.exists(), "dry run must not delete the directory"

    # Confirm=True must wipe the .bicameral/ dir.
    result = await handle_reset(ctx, confirm=True, wipe_mode="full")
    assert result.wiped is True
    assert result.wipe_mode == "full"
    assert result.cursors_before == 2
    # SurrealKV recreates its parent dir on reconnect, but config and events must be gone.
    assert not (bicameral_dir / "config.yaml").exists(), "config.yaml must be deleted"
    assert not (bicameral_dir / "events").exists(), "events/ dir must be deleted"

    # Server must be usable immediately after (schema reinitialised).
    post_cursors = await ledger.get_all_source_cursors("repo-full")
    assert post_cursors == []

    _reset()
