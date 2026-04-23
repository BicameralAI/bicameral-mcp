"""Tests for pending_grounding_checks in LinkCommitResponse.

Covers:
1. test_pending_grounding_checks_for_ungrounded_decisions — ingest a decision with no
   code_regions → link_commit returns pending_grounding_checks containing that decision
2. test_pending_grounding_checks_symbol_not_found — ingest a decision with a binding,
   then simulate symbol disappearing → link_commit emits grounding check for that decision
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit


# ── Helpers ───────────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _seed_repo(repo_root: Path, body: str = "") -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@e.com")
    _git(repo_root, "config", "user.name", "tester")
    source = dedent(body).strip() + "\n" if body else "# placeholder\n"
    (repo_root / "impl.py").write_text(source)
    _git(repo_root, "add", ".")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")


@pytest.fixture
def _isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "repo"
    _seed_repo(
        repo_root,
        """
        def fetch_user(user_id: int):
            return {"id": user_id, "name": "test"}
        """,
    )
    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    yield repo_root
    reset_ledger_singleton()


# ── 1. Ungrounded decisions surface in pending_grounding_checks ───────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_pending_grounding_checks_for_ungrounded_decisions(_isolated_ledger):
    """Ingest a decision with no code_regions.

    link_commit should return pending_grounding_checks containing that decision.
    """
    repo_root = _isolated_ledger
    ctx = BicameralContext.from_env()

    # Ingest a decision with NO code_regions (stays ungrounded)
    payload = {
        "query": "fetch user by id",
        "repo": str(repo_root),
        "mappings": [
            {
                "span": {
                    "source_type": "manual",
                    "text": "User fetch should use the database not a mock",
                    "source_ref": "test-grounding",
                },
                "intent": "User fetch should use the database not a mock",
                "symbols": [],
                "code_regions": [],  # no grounding
            }
        ],
    }

    ingest_resp = await handle_ingest(ctx, payload)
    assert ingest_resp.ingested
    assert ingest_resp.stats.ungrounded == 1
    assert len(ingest_resp.pending_grounding_decisions) == 1

    # link_commit should surface the ungrounded decision in pending_grounding_checks
    lc_resp = await handle_link_commit(ctx, "HEAD")
    assert lc_resp.synced

    grounding_decision_ids = [c["decision_id"] for c in lc_resp.pending_grounding_checks]
    assert len(grounding_decision_ids) > 0, (
        "expected at least one pending_grounding_check for the ungrounded decision"
    )
    # The ungrounded decision should appear
    reasons = [c.get("reason") for c in lc_resp.pending_grounding_checks]
    assert "ungrounded" in reasons


# ── 2. Symbol disappeared → grounding check emitted ──────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_pending_grounding_checks_symbol_not_found(_isolated_ledger):
    """Ingest a decision with a binding, then simulate symbol disappearing.

    link_commit should emit a grounding check for that decision when
    resolve_symbol_lines returns None.
    """
    from unittest.mock import patch

    repo_root = _isolated_ledger
    ctx = BicameralContext.from_env()

    # Ingest a decision WITH a code region (starts as pending/grounded)
    payload = {
        "query": "fetch user implementation",
        "repo": str(repo_root),
        "mappings": [
            {
                "span": {
                    "source_type": "manual",
                    "text": "Fetch user from DB by ID",
                    "source_ref": "test-symbol-disappear",
                },
                "intent": "Fetch user from DB by ID",
                "symbols": ["fetch_user"],
                "code_regions": [
                    {
                        "file_path": "impl.py",
                        "symbol": "fetch_user",
                        "start_line": 1,
                        "end_line": 3,
                        "type": "function",
                        "purpose": "fetch user by id",
                    }
                ],
            }
        ],
    }

    ingest_resp = await handle_ingest(ctx, payload)
    assert ingest_resp.ingested
    # Decision should be grounded (has code_region)
    assert ingest_resp.stats.regions_linked >= 1

    # Make a new commit that modifies impl.py so ingest_commit does a fresh sweep
    # (the ledger-level idempotency check uses last_synced_commit == commit_hash)
    (repo_root / "impl.py").write_text(
        "def fetch_user_v2(user_id: int):\n    return {'id': user_id}\n"
    )
    _git(repo_root, "add", "impl.py")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "rename symbol")

    # Invalidate the within-call sync cache so the handler runs a real sweep
    from handlers.link_commit import invalidate_sync_cache
    invalidate_sync_cache(ctx)

    # Simulate the old symbol (fetch_user) not being found in the new commit
    with patch("ledger.status.resolve_symbol_lines", return_value=None):
        lc_resp = await handle_link_commit(ctx, "HEAD")

    assert lc_resp.synced

    # A grounding check should be emitted for the decision whose symbol disappeared
    grounding_checks = lc_resp.pending_grounding_checks
    disappeared_checks = [c for c in grounding_checks if c.get("reason") == "symbol_disappeared"]
    assert len(disappeared_checks) >= 1, (
        f"Expected symbol_disappeared grounding check, got: {grounding_checks}"
    )
    assert disappeared_checks[0]["symbol"] == "fetch_user"
