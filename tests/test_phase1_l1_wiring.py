"""L1 drift ladder wiring regression tests (bicameral-mcp v0.4.5).

These lock in the two fixes that make `pending → reflected` actually work:

1. **Baseline hash stamping at ingest** — `ingest_payload` resolves HEAD and
   computes a `content_hash` for every grounded region, then derives the
   intent's initial status. Before v0.4.5 an unconditional `"pending"` was
   persisted, and without a baseline hash the subsequent `link_commit`
   sweep could never mark the decision reflected or drifted.

2. **Empty-hash backfill sweep** — `handle_link_commit` walks regions with
   empty `content_hash` scoped to the active repo and self-heals them via
   `HashDriftAnalyzer`, which adopts the current git state as the baseline.
   This rescues ledgers ingested on older versions.

The tests use a throwaway git repo under tmp_path so they don't depend on
HEAD of the bicameral repo staying stable.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.decision_status import handle_decision_status
from handlers.link_commit import handle_link_commit


# ── Tiny git repo fixture ─────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(body).lstrip("\n"))


def _seed_repo(root: Path, initial_body: str) -> str:
    """Create a git repo at ``root`` with a single python file containing
    a ``calculate_discount`` function. Returns the initial commit SHA."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _write(root / "pricing.py", initial_body)
    _git(root, "add", "pricing.py")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return _git(root, "rev-parse", "HEAD")


def _commit_edit(root: Path, new_body: str, msg: str) -> str:
    _write(root / "pricing.py", new_body)
    _git(root, "add", "pricing.py")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _isolated_ledger(monkeypatch, tmp_path):
    """Fresh in-memory ledger + tmp git repo per test."""
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
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    yield repo_root
    reset_ledger_singleton()


def _payload_for(symbol: str, intent: str, file_path: str, start: int, end: int, repo: str) -> dict:
    """Build a minimal ingest payload with pre-resolved code regions.

    We skip auto-grounding so these tests stay laser-focused on the drift
    wiring — the regions are handed in directly.
    """
    return {
        "query": intent,
        "repo": repo,
        "analyzed_at": "2026-04-14T00:00:00Z",
        "mappings": [
            {
                "span": {
                    "span_id": "p1-0",
                    "source_type": "transcript",
                    "text": intent,
                    "source_ref": "phase1-test",
                },
                "intent": intent,
                "symbols": [symbol],
                "code_regions": [
                    {
                        "file_path": file_path,
                        "symbol": symbol,
                        "type": "function",
                        "start_line": start,
                        "end_line": end,
                        "purpose": "pricing discount rule",
                    }
                ],
                "dependency_edges": [],
            }
        ],
    }


def _ctx() -> BicameralContext:
    return BicameralContext.from_env()


# ── Regression tests ──────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_ingest_of_existing_symbol_is_reflected_immediately(_isolated_ledger):
    """A region grounded against code that exists at HEAD must be born
    reflected — not stuck at pending because no baseline hash was stamped.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    payload = _payload_for(
        symbol="calculate_discount",
        intent="Apply 10% discount on orders of $100 or more",
        file_path="pricing.py",
        start=1,
        end=4,
        repo=str(repo_root),
    )
    result = await ledger.ingest_payload(payload)
    assert result["stats"]["regions_linked"] == 1, (
        f"Expected 1 region linked, got stats={result['stats']!r}"
    )

    ctx = _ctx()
    status = await handle_decision_status(ctx, filter="all")
    assert status.summary.get("reflected", 0) == 1, (
        f"Expected 1 reflected intent immediately after ingest, "
        f"got summary={status.summary!r}"
    )
    assert status.summary.get("pending", 0) == 0, (
        f"No intent should be pending when the grounded symbol exists at HEAD, "
        f"got summary={status.summary!r}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_edit_to_grounded_symbol_flips_to_drifted(_isolated_ledger):
    """After ingest, a real code edit on the grounded region must flip the
    decision's status from reflected to drifted on the next link_commit.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    payload = _payload_for(
        symbol="calculate_discount",
        intent="Apply 10% discount on orders of $100 or more",
        file_path="pricing.py",
        start=1,
        end=4,
        repo=str(repo_root),
    )
    await ledger.ingest_payload(payload)

    ctx = _ctx()
    pre = await handle_decision_status(ctx, filter="all")
    assert pre.summary.get("reflected", 0) == 1

    # Invert the discount threshold — real semantic change, not cosmetic
    _commit_edit(
        repo_root,
        """
        def calculate_discount(order_total):
            if order_total >= 500:
                return order_total * 0.25
            return 0
        """,
        "tighten discount thresholds",
    )

    await handle_link_commit(ctx, "HEAD")
    post = await handle_decision_status(ctx, filter="all")
    assert post.summary.get("drifted", 0) == 1, (
        f"Edit to grounded symbol must flip to drifted, "
        f"got summary={post.summary!r}"
    )
    assert post.summary.get("reflected", 0) == 0


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_phantom_range_stays_pending(_isolated_ledger):
    """A region whose line range doesn't exist at HEAD must stay
    ungrounded/pending — not crash, not false-flip to reflected.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    payload = _payload_for(
        symbol="phantom_symbol",
        intent="This decision points at code that was never written",
        file_path="does_not_exist.py",
        start=1,
        end=20,
        repo=str(repo_root),
    )
    await ledger.ingest_payload(payload)

    ctx = _ctx()
    status = await handle_decision_status(ctx, filter="all")
    assert status.summary.get("reflected", 0) == 0, (
        f"Phantom range must not report as reflected, got {status.summary!r}"
    )
    # Either ungrounded (region linked but no hashable content) or pending
    # is acceptable — the important thing is the region never masquerades
    # as reflected when nothing actually exists to hash.
    bad = [d for d in status.decisions if d.status == "reflected"]
    assert not bad, f"Unexpected reflected decisions: {[d.description for d in bad]}"


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_backfill_heals_legacy_empty_hash_regions(_isolated_ledger):
    """Simulate a pre-v0.4.5 ledger by clearing content_hash on an ingested
    region and flipping its status to pending. The next link_commit must
    run the backfill sweep and flip the decision to reflected without
    needing the commit to touch the file.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    payload = _payload_for(
        symbol="calculate_discount",
        intent="Apply 10% discount on orders of $100 or more",
        file_path="pricing.py",
        start=1,
        end=4,
        repo=str(repo_root),
    )
    await ledger.ingest_payload(payload)

    # Force the region back into the pre-v0.4.5 shape: empty content_hash,
    # intent back to pending. This is the state every Accountable-style
    # bulk ingest left behind.
    inner = getattr(ledger, "_inner", ledger)
    client = inner._client
    await client.query("UPDATE code_region SET content_hash = ''")
    await client.query("UPDATE intent SET status = 'pending'")

    pre = await client.query("SELECT status FROM intent")
    assert any(r.get("status") == "pending" for r in pre), (
        "Precondition: the intent should be pending before backfill runs"
    )

    ctx = _ctx()
    # handle_decision_status auto-calls handle_link_commit, which runs the
    # backfill sweep before the normal drift loop. No code edit, no commit.
    status = await handle_decision_status(ctx, filter="all")
    assert status.summary.get("reflected", 0) == 1, (
        f"Backfill must self-heal pre-v0.4.5 empty-hash regions, "
        f"got summary={status.summary!r}"
    )
    assert status.summary.get("pending", 0) == 0
