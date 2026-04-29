"""v0.4.11 latent drift fix — regression tests.

Two layers:

  1. **Range-diff sweep** — when ``last_synced_commit`` lags HEAD by N
     commits, ``handle_link_commit`` now sweeps every file touched in
     that range, not just HEAD's own diff. Catches drift introduced
     in commits between syncs.

  2. **Distinct intent counters** — ``decisions_drifted`` and
     ``decisions_reflected`` now count distinct intent_ids that
     flipped, not (region, intent) pairs. A decision with N regions
     all flipping in the same sweep counts as 1, not N.

Also covers the new response fields (``sweep_scope``, ``range_size``)
and the fall-back logic when a range is unreachable (force-push,
shallow clone).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from handlers.link_commit import handle_link_commit
from ledger.status import get_changed_files, get_changed_files_in_range

# ── Helpers ─────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _seed_repo(repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "t@e.com")
    _git(repo_root, "config", "user.name", "t")
    (repo_root / "pricing.py").write_text(
        dedent("""
        def calculate_discount(order_total):
            if order_total >= 100:
                return order_total * 0.10
            return 0
    """).strip()
        + "\n"
    )
    (repo_root / "auth.py").write_text(
        dedent("""
        def validate_token(token):
            if not token:
                return False
            return len(token) > 10
    """).strip()
        + "\n"
    )
    _git(repo_root, "add", ".")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")
    return _git(repo_root, "rev-parse", "HEAD")


def _commit_edit(repo_root: Path, file: str, body: str, message: str) -> str:
    (repo_root / file).write_text(dedent(body).strip() + "\n")
    _git(repo_root, "add", file)
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)
    return _git(repo_root, "rev-parse", "HEAD")


@pytest.fixture
def _isolated_ledger(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "repo"
    _seed_repo(repo_root)
    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo_root)
    reset_ledger_singleton()
    yield repo_root
    reset_ledger_singleton()


def _ctx() -> BicameralContext:
    return BicameralContext.from_env()


# ── get_changed_files_in_range — pure helper ──────────────────────


def test_range_diff_returns_files_touched_in_range(tmp_path):
    repo = tmp_path / "repo"
    base_sha = _seed_repo(repo)
    sha2 = _commit_edit(repo, "pricing.py", "x = 1", "edit pricing")
    sha3 = _commit_edit(repo, "auth.py", "y = 2", "edit auth")

    files = get_changed_files_in_range(base_sha, sha3, str(repo))
    assert files is not None
    assert set(files) == {"pricing.py", "auth.py"}


def test_range_diff_empty_when_same_sha(tmp_path):
    repo = tmp_path / "repo"
    sha = _seed_repo(repo)
    files = get_changed_files_in_range(sha, sha, str(repo))
    assert files == []


def test_range_diff_returns_none_on_unreachable_ref(tmp_path):
    repo = tmp_path / "repo"
    sha = _seed_repo(repo)
    # Garbage SHA that doesn't exist in this repo
    bogus = "deadbeef" + "0" * 32
    files = get_changed_files_in_range(bogus, sha, str(repo))
    assert files is None


def test_get_changed_files_unchanged_for_single_commit(tmp_path):
    """The pre-v0.4.11 head-only helper still works for fresh-repo
    bootstraps."""
    repo = tmp_path / "repo"
    sha = _seed_repo(repo)
    files = get_changed_files(sha, str(repo))
    assert set(files) == {"pricing.py", "auth.py"}


# ── handle_link_commit — sweep_scope semantics ─────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_first_sync_uses_head_only_scope(_isolated_ledger):
    """No cursor exists yet, so the first sync falls back to head-only."""
    ledger = get_ledger()
    await ledger.connect()

    ctx = _ctx()
    r = await handle_link_commit(ctx, "HEAD")

    assert r.sweep_scope == "head_only"
    assert r.range_size == 2  # pricing.py + auth.py from seed commit


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_second_sync_after_gap_uses_range_diff(_isolated_ledger):
    """After the first sync stamps the cursor, a subsequent sync
    against an advanced HEAD should use range_diff and find every
    file touched between cursor and HEAD — including files NOT in
    the head commit's own diff.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    # Sync #1 — stamps the cursor at the seed commit
    ctx1 = _ctx()
    r1 = await handle_link_commit(ctx1, "HEAD")
    assert r1.sweep_scope == "head_only"
    seed_sha = r1.commit_hash

    # Two commits, two different files
    sha2 = _commit_edit(
        repo_root,
        "pricing.py",
        "def calculate_discount(t):\n    return t * 0.5",
        "rewrite pricing",
    )
    sha3 = _commit_edit(
        repo_root,
        "auth.py",
        "def validate_token(t):\n    return False",
        "rewrite auth",
    )
    assert sha3 != sha2 != seed_sha

    # Sync #2 — should diff seed..HEAD and pick up BOTH files,
    # not just auth.py (which is the head commit's own diff).
    # Fresh ctx so the in-call sync cache is empty.
    # Fresh ctx (empty within-call sync cache) but same ledger instance
    # so the cursor stamped by sync #1 persists across this call.
    ctx2 = _ctx()
    r2 = await handle_link_commit(ctx2, "HEAD")

    assert r2.sweep_scope == "range_diff", f"Expected range_diff after gap, got {r2.sweep_scope}"
    assert r2.range_size >= 2, (
        f"Expected range sweep to cover both pricing.py + auth.py "
        f"(range_size>=2), got range_size={r2.range_size}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_pre_v0411_head_only_would_have_missed_intermediate_drift(
    _isolated_ledger,
):
    """Latent drift demonstration: with the OLD head-only behavior,
    a commit that drifts pricing.py followed by an unrelated commit
    that touches README.md would have left the pricing drift invisible
    until pricing.py was edited again. v0.4.11's range_diff catches
    it on the next link_commit.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    # Sync #1 stamps cursor
    ctx1 = _ctx()
    await handle_link_commit(ctx1, "HEAD")

    # Drift commit
    _commit_edit(
        repo_root,
        "pricing.py",
        "def calculate_discount(t):\n    return t * 999",  # nonsense
        "drift pricing",
    )
    # Unrelated subsequent commit
    (repo_root / "README.md").write_text("# repo\n")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add readme")

    # Fresh ctx; same ledger so cursor persists.
    ctx2 = _ctx()
    r2 = await handle_link_commit(ctx2, "HEAD")

    # Range should include pricing.py + README.md, NOT just README.md
    assert r2.sweep_scope == "range_diff"
    assert r2.range_size >= 2


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_sync_to_same_sha_fast_paths_with_head_only_scope(_isolated_ledger):
    """Calling link_commit twice on the same SHA: second hits the
    fast-path and returns sweep_scope='head_only' (no work done,
    cursor unchanged)."""
    ledger = get_ledger()
    await ledger.connect()
    ctx = _ctx()
    await handle_link_commit(ctx, "HEAD")

    # Fresh ctx (clears v0.4.8 within-call dedup cache) but same ledger
    # so the cursor persists; second call should hit the ledger-level
    # idempotency fast-path.
    ctx2 = _ctx()
    r2 = await handle_link_commit(ctx2, "HEAD")

    # Fast-path returns "already_synced" + head_only scope, range_size=0
    assert r2.reason == "already_synced"
    assert r2.sweep_scope == "head_only"
    assert r2.range_size == 0


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_unreachable_base_sha_falls_back_to_head_only(
    _isolated_ledger,
    monkeypatch,
):
    """If ``last_synced_commit`` is unreachable (force-push, shallow
    clone), the range diff returns None and we fall back to head-only.
    The sync still completes — we just lose the intermediate-commit
    coverage for one cycle.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    # Inject a bogus cursor by patching get_sync_state to return a
    # SHA that doesn't exist in the repo.
    from ledger import adapter as adapter_mod

    bogus = "deadbeef" + "0" * 32

    real_get_sync_state = adapter_mod.get_sync_state

    async def _bogus_get_sync_state(client, repo_path):
        return {"last_synced_commit": bogus}

    monkeypatch.setattr(adapter_mod, "get_sync_state", _bogus_get_sync_state)

    ctx = _ctx()
    r = await handle_link_commit(ctx, "HEAD")

    # Should fall back to head_only, NOT crash
    assert r.sweep_scope == "head_only"
    assert r.range_size >= 1


# ── Distinct intent counters ───────────────────────────────────────


def test_link_commit_response_contract_has_new_fields():
    """LinkCommitResponse v0.4.11 contract has sweep_scope + range_size."""
    from contracts import LinkCommitResponse

    fields = LinkCommitResponse.model_fields
    assert "sweep_scope" in fields
    assert "range_size" in fields
    # Defaults: head_only / 0 — backward compat for callers that don't set them
    inst = LinkCommitResponse(
        commit_hash="abc",
        synced=True,
        reason="new_commit",
    )
    assert inst.sweep_scope == "head_only"
    assert inst.range_size == 0


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_multi_region_edits_emit_pending_checks_per_region(
    _isolated_ledger,
):
    """v3 baseline: one intent mapped to N regions → N pending checks on
    a multi-region edit; ``decisions_drifted`` stays 0 until the caller
    LLM resolves them.

    Pre-v3 this test asserted ``decisions_drifted == 1`` on the v0.4.11
    dedup-by-intent_id invariant: an intent flipping on N regions in the
    same sweep should be counted once. That invariant still holds under
    v3 in spirit, but the semantics flipped — hash-only changes project
    to PENDING, not DRIFTED, because REFLECTED/DRIFTED both require a
    verdict (plan 2026-04-20). A full verification of the dedup invariant
    now requires seeding compliance_check rows with differing verdicts;
    that will move into a resolve_compliance-level test once Phase 3
    lands.

    The v3-visible claim this test makes is more specific: the
    pending_compliance_checks batch includes one entry PER (intent,
    region) pair. The caller LLM needs to see each region's code_body
    independently so it can issue per-region verdicts — the intent
    aggregates as compliant only if every linked region is compliant.
    """
    repo_root = _isolated_ledger
    ledger = get_ledger()
    await ledger.connect()

    # Append a second function so we have two regions in pricing.py
    (repo_root / "pricing.py").write_text(
        dedent("""
        def calculate_discount(order_total):
            if order_total >= 100:
                return order_total * 0.10
            return 0


        def calculate_tax(order_total):
            return order_total * 0.08
    """).strip()
        + "\n"
    )
    _git(repo_root, "add", "pricing.py")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add tax")

    payload = {
        "query": "Apply 10% discount and 8% tax on orders",
        "repo": str(repo_root),
        "mappings": [
            {
                "span": {
                    "span_id": "p1-0",
                    "source_type": "transcript",
                    "text": "Apply 10% discount and 8% tax on orders",
                    "source_ref": "v0411-test",
                },
                "intent": "Apply 10% discount and 8% tax on orders",
                "symbols": ["calculate_discount", "calculate_tax"],
                "code_regions": [
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                    },
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_tax",
                        "type": "function",
                        "start_line": 7,
                        "end_line": 8,
                    },
                ],
            }
        ],
    }
    await ledger.ingest_payload(payload)

    # First sync — stamp baselines for both regions
    ctx = _ctx()
    await handle_link_commit(ctx, "HEAD")

    # Now drift BOTH regions in one commit
    (repo_root / "pricing.py").write_text(
        dedent("""
        def calculate_discount(order_total):
            return order_total * 999  # nonsense


        def calculate_tax(order_total):
            return order_total * 999  # nonsense
    """).strip()
        + "\n"
    )
    _git(repo_root, "add", "pricing.py")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "drift both")

    # Fresh ctx; same ledger so the prior baselines + cursor persist.
    ctx2 = _ctx()
    r2 = await handle_link_commit(ctx2, "HEAD")

    assert r2.decisions_drifted == 0, (
        f"v3: hash-change alone cannot declare DRIFTED — requires a "
        f"non-compliant verdict. Got decisions_drifted={r2.decisions_drifted}."
    )
    assert r2.decisions_reflected == 0

    # Both regions should produce pending checks — one per (intent, region).
    assert len(r2.pending_compliance_checks) == 2, (
        f"Expected 2 pending checks (one per region), got "
        f"{len(r2.pending_compliance_checks)}: {r2.pending_compliance_checks!r}"
    )

    # Same intent across both checks (proves the shared-intent case).
    intent_ids = {p.decision_id for p in r2.pending_compliance_checks}
    assert len(intent_ids) == 1, (
        f"Multi-region test: pending checks should share one decision_id, got {intent_ids}"
    )

    # Distinct region_ids — the caller needs independent verdicts per region.
    region_ids = {p.region_id for p in r2.pending_compliance_checks}
    assert len(region_ids) == 2, f"Expected 2 distinct region_ids in the batch, got {region_ids}"

    # Phase is drift (hash-mismatch triggered re-emission).
    phases = {p.phase for p in r2.pending_compliance_checks}
    assert phases == {"drift"}, f"Expected drift-phase checks, got {phases}"
