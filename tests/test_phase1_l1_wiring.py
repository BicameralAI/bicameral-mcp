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
    # v0.4.6: the outer conftest autouse fixture set authoritative_ref to
    # the bicameral submodule's current branch (e.g. "chore/bump-v0.4.6").
    # Our tmp repo is on "main" — override the env var so the pollution
    # guard treats "main" as authoritative within this test scope.
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
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
async def test_ingest_of_existing_symbol_is_pending_until_verified(_isolated_ledger):
    """v3 baseline: grounded-but-unverified regions project as PENDING.

    Pre-v3 this test asserted REFLECTED immediately after ingest — the
    "born reflected" shortcut where a hash match was treated as proof that
    the code implemented the decision. The unified-compliance plan
    (2026-04-20-ingest-time-verification.md) explicitly breaks that
    shortcut: REFLECTED requires a compliance_check row from a caller-LLM
    verdict. Ingest alone gets you the baseline hash and a region link —
    it does NOT constitute verification.

    The drift-sweep auto-chain on ingest will emit a pending_compliance_check
    for this intent; the caller LLM is expected to resolve it. Until then,
    PENDING is the honest status.
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
    assert status.summary.get("reflected", 0) == 0, (
        f"v3 must not auto-promote to REFLECTED without a verdict, "
        f"got summary={status.summary!r}"
    )
    assert status.summary.get("pending", 0) == 1, (
        f"Expected 1 pending intent (grounded but unverified), "
        f"got summary={status.summary!r}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_hash_change_alone_does_not_flip_status_without_verdict(_isolated_ledger):
    """v3 baseline: a code edit invalidates the cache but cannot declare
    drift by itself.

    Pre-v3 this test asserted REFLECTED → DRIFTED across an edit, treating
    hash mismatch as proof of drift. Under v3 that is semantically
    insufficient — "the bytes changed" does not entail "the decision is
    no longer satisfied." A cosmetic refactor, rename, or reformat can
    change the hash while preserving compliance.

    The honest post-edit status is PENDING: the cache has no verdict for
    the new content_hash, so we need the caller LLM to evaluate before
    calling it drifted. The drift-sweep emits a pending_compliance_check
    for the new shape; resolve_compliance flips it to REFLECTED or DRIFTED.
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
    assert pre.summary.get("pending", 0) == 1, (
        f"Pre-edit baseline is PENDING under v3 (grounded, unverified), "
        f"got summary={pre.summary!r}"
    )

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
    assert post.summary.get("drifted", 0) == 0, (
        f"v3 must not declare DRIFTED from a hash change alone — requires "
        f"a non-compliant verdict. Got summary={post.summary!r}"
    )
    assert post.summary.get("pending", 0) == 1, (
        f"Post-edit cache miss projects PENDING until resolve_compliance runs, "
        f"got summary={post.summary!r}"
    )


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
async def test_backfill_restores_hash_but_stays_pending_without_verdict(_isolated_ledger):
    """v3 baseline: backfill re-stamps content_hash but does not auto-heal
    to REFLECTED.

    Pre-v3 this test asserted that backfill self-heals legacy empty-hash
    regions to REFLECTED. That shortcut is gone under v3: restoring the
    hash only gives us a cache key; REFLECTED still requires a caller-LLM
    verdict against that hash. Backfill's job shrinks to "make the region
    addressable by the compliance cache" — verification is a separate
    step.

    Post-backfill, the next drift-sweep emits a pending_compliance_check
    for the newly-hashed region. The caller LLM resolves it via
    bicameral.resolve_compliance; only then does status flip to REFLECTED.
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
    await client.query("UPDATE decision SET status = 'pending'")

    pre = await client.query("SELECT status FROM decision")
    assert any(r.get("status") == "pending" for r in pre), (
        "Precondition: the intent should be pending before backfill runs"
    )

    ctx = _ctx()
    # handle_decision_status auto-calls handle_link_commit, which runs the
    # backfill sweep before the normal drift loop. Backfill re-stamps
    # content_hash; cache lookup finds no verdict → stays PENDING.
    status = await handle_decision_status(ctx, filter="all")
    assert status.summary.get("reflected", 0) == 0, (
        f"v3 backfill must not auto-heal to REFLECTED without a verdict, "
        f"got summary={status.summary!r}"
    )
    assert status.summary.get("pending", 0) == 1, (
        f"Post-backfill region is hashed but unverified → PENDING, "
        f"got summary={status.summary!r}"
    )

    # Defensive: confirm backfill actually re-stamped the content_hash
    # (the cache-key is now populated even though the verdict isn't).
    post_rows = await client.query("SELECT content_hash FROM code_region")
    hashes = [r.get("content_hash", "") for r in post_rows]
    assert any(h for h in hashes), (
        f"Backfill should have populated content_hash, got {hashes!r}"
    )
