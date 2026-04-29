"""Exhaustive regression matrix for ephemeral/authoritative edge cases (v1).

20 scenarios covering the full lifecycle of compliance verdicts across branch
boundaries, process restarts, hash-keyed lookups, and authority resolution.

Each test is tagged:
  [PASS]    — expected to pass in V1 (current code)
  [xfail V2] — expected to fail in V1; blocked on V2 feature

V2 features referenced in xfail tests:
  - Branch-switch invalidation: clear stale ephemeral verdicts when session
    moves to a diverged branch
  - Ephemeral promotion: mark compliance_check.ephemeral=False when the
    same hash lands on the authoritative branch post-merge
  - Branch-delta sweep: git diff <auth>...HEAD coverage for first feature sync
  - Ephemeral first-write-wins guard: prevent an ephemeral write from blocking
    a non-ephemeral write for the same (decision, region, hash)

Scenario matrix:
  E1  — authoritative branch full cycle → reflected, ephemeral=False     [PASS]
  E2  — feature branch full cycle → reflected, ephemeral=True             [PASS]
  E3  — fast-forward merge → verdict survives same hash                   [PASS]
  E4  — squash merge → same content hash → reflected                      [PASS]
  E5  — content change → drifted (prior compliant verdict exists)         [PASS]
  E6  — branch switch A→diverged B → status drifted (V2: hash update ungated)  [PASS V2]
  E7  — feature→main after merge → ephemeral promoted to False          [PASS V2]
  E8  — detached HEAD → non-ephemeral (safe default)                      [PASS]
  E9  — process restart → flag lost, status still correct                 [PASS]
  E10 — idempotent resolve_compliance (UNIQUE upsert)                     [PASS]
  E11 — flow_id mismatch → ephemeral=False, status still correct          [PASS]
  E12 — branch-delta sweep detects drift from earlier feature commits   [PASS V2]
  E13 — rebase onto main: same content, new SHA → verdict carries over    [PASS]
  E14 — deleted branch → verdict survives (hash-keyed)                    [PASS]
  E15 — authoritative_ref="" → degraded safe mode, ephemeral=False        [PASS]
  E16 — resolve_compliance without prior link_commit → reflected          [PASS]
  E17 — ephemeral first-write-wins → promoted by resolve_compliance      [PASS V2]
  E18 — bind to branch-local file succeeds (ephemeral-aware ref)         [PASS]
  E19 — bind to modified function uses branch hash, not main hash        [PASS]
  E20 — bind+link_commit hash consistency: no phantom drifted after resolve [PASS]
  E21 — ungrounded → feature branch bind → reflected + ephemeral=True   [PASS]
  E22 — switch back to main: no stale ephemeral 'reflected' (→ drifted)  [PASS]
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import reset_ledger_singleton
from context import BicameralContext
from handlers.bind import handle_bind
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit, invalidate_sync_cache
from handlers.resolve_compliance import handle_resolve_compliance


# ── Helpers ───────────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(repo: Path, msg: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)


def _seed_repo(repo: Path, files: dict[str, str]) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "tester")
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).strip() + "\n")
    _commit(repo, "seed")


def _checkout(repo: Path, branch: str, *, create: bool = False) -> None:
    if create:
        _git(repo, "checkout", "-b", branch)
    else:
        _git(repo, "checkout", branch)


def _merge(repo: Path, branch: str, *, squash: bool = False, no_ff: bool = False) -> None:
    if squash:
        _git(repo, "merge", "--squash", branch)
        _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"Squash-merge {branch}")
    elif no_ff:
        _git(repo, "-c", "commit.gpgsign=false", "merge", "--no-ff", "-m", f"Merge {branch}", branch)
    else:
        _git(repo, "-c", "commit.gpgsign=false", "merge", branch)


async def _get_client(ctx):
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()
    inner = getattr(ledger, "_inner", ledger)
    return inner._client


async def _get_decision_status(ctx, decision_id: str) -> str:
    client = await _get_client(ctx)
    rows = await client.query(f"SELECT status FROM {decision_id} LIMIT 1")
    return str(rows[0]["status"]) if rows else "unknown"


async def _get_compliance_checks(ctx, decision_id: str) -> list[dict]:
    client = await _get_client(ctx)
    rows = await client.query(
        "SELECT verdict, ephemeral, content_hash, phase FROM compliance_check "
        "WHERE decision_id = $d",
        {"d": decision_id},
    )
    return rows or []


def _payload(
    repo: Path,
    *,
    text: str,
    intent: str,
    code_regions: list[dict] | None = None,
    source_ref: str = "eph-test",
) -> dict:
    return {
        "query": intent,
        "repo": str(repo),
        "mappings": [
            {
                "span": {
                    "source_type": "manual",
                    "text": text,
                    "source_ref": source_ref,
                },
                "intent": intent,
                "code_regions": code_regions or [],
            }
        ],
    }


async def _ingest_and_bind(
    ctx,
    repo: Path,
    *,
    intent: str,
    file_path: str,
    symbol_name: str,
    start_line: int,
    end_line: int,
) -> tuple[str, str, str]:
    """Ingest decision + bind region. Returns (decision_id, region_id, content_hash)."""
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text=intent, intent=intent, code_regions=[]),
    )
    assert ingest.ingested, f"ingest failed: {ingest}"
    decision_id = ingest.created_decisions[0].decision_id

    bind_resp = await handle_bind(ctx, [{
        "decision_id": decision_id,
        "file_path": file_path,
        "symbol_name": symbol_name,
        "start_line": start_line,
        "end_line": end_line,
    }])
    assert bind_resp.bindings, "no bind results"
    assert not bind_resp.bindings[0].error, f"bind error: {bind_resp.bindings[0].error}"
    return decision_id, bind_resp.bindings[0].region_id, bind_resp.bindings[0].content_hash


async def _resolve_verdict(
    ctx,
    lc,
    decision_id: str,
    *,
    verdict: str = "compliant",
    phase: str = "ingest",
) -> object:
    """Find pending check for decision and resolve it."""
    pending = [p for p in lc.pending_compliance_checks if p.decision_id == decision_id]
    assert pending, (
        f"No pending check for {decision_id}. "
        f"All checks: {[(p.decision_id, p.content_hash[:8]) for p in lc.pending_compliance_checks]}"
    )
    p = pending[0]
    return await handle_resolve_compliance(
        ctx,
        phase=phase,
        verdicts=[{
            "decision_id": decision_id,
            "region_id": p.region_id,
            "content_hash": p.content_hash,
            "verdict": verdict,
            "confidence": "high",
            "explanation": "test",
        }],
        flow_id=lc.flow_id,
    )


# ── Fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def _eph_repo(monkeypatch, tmp_path):
    """Fresh git repo on `main` with in-memory ledger.

    Pins BICAMERAL_AUTHORITATIVE_REF=main explicitly to override the conftest
    autouse fixture, which would otherwise set it to the test runner's current
    branch (the bicameral submodule checkout branch).
    """
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo = tmp_path / "repo"
    _seed_repo(repo, {
        "src/calc.py": """
            def rate(order_total: float) -> float:
                return order_total * 0.1
        """,
    })
    monkeypatch.setenv("REPO_PATH", str(repo))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo)
    reset_ledger_singleton()
    yield repo
    reset_ledger_singleton()


# ── E1: Authoritative branch full cycle ───────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e01_authoritative_branch_full_cycle(_eph_repo):
    """[PASS] Full ingest→bind→link_commit→resolve_compliance cycle on main.

    Invariants:
    - link_commit.ephemeral is False on authoritative branch
    - compliance_check.ephemeral is False
    - decision.status transitions pending → reflected
    """
    repo = _eph_repo
    ctx = BicameralContext.from_env()

    # Ingest with code_regions so the binding exists before the internal link_commit.
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="10% discount rule", intent="Apply 10% discount on all orders",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate calc",
                 }]),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id

    lc = await handle_link_commit(ctx, "HEAD")
    assert lc.ephemeral is False, "main branch link_commit must be non-ephemeral"

    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted, f"resolve rejected: {rc.rejected}"

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", f"Expected reflected, got {status}"

    checks = await _get_compliance_checks(ctx, decision_id)
    assert checks, "no compliance_check written"
    assert checks[0]["ephemeral"] is False or checks[0]["ephemeral"] == False, (
        f"Expected ephemeral=False on main, got {checks[0]['ephemeral']}"
    )


# ── E2: Feature branch full cycle ─────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e02_feature_branch_full_cycle(_eph_repo):
    """[PASS] Full cycle on a feature branch: verdict stored as ephemeral=True.

    Invariants:
    - link_commit.ephemeral is True (commit not reachable from main)
    - compliance_check.ephemeral is True
    - decision.status still transitions to reflected (ephemeral verdicts count)
    """
    repo = _eph_repo

    # Create feature branch and modify the file.
    _checkout(repo, "feat/pricing", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.15\n"
    )
    _commit(repo, "bump rate to 15%")

    # Create context while on feature branch (authoritative_ref=main still).
    ctx = BicameralContext.from_env()

    # Ingest on the feature branch — code_regions reference the original file on main.
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Pricing rate", intent="Apply rate to order total",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate calc",
                 }]),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id

    # link_commit: feature commit is not reachable from main → ephemeral=True.
    lc = await handle_link_commit(ctx, "HEAD")
    assert lc.ephemeral is True, (
        f"Feature branch link_commit must be ephemeral=True, got {lc.ephemeral}"
    )

    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted, f"resolve rejected: {rc.rejected}"

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", f"Expected reflected, got {status}"

    checks = await _get_compliance_checks(ctx, decision_id)
    assert checks, "no compliance_check written"
    assert checks[0]["ephemeral"] is True or checks[0]["ephemeral"] == True, (
        f"Expected ephemeral=True on feature branch, got {checks[0]['ephemeral']}"
    )


# ── E3: Fast-forward merge → verdict survives same hash ───────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e03_ff_merge_verdict_survives(_eph_repo):
    """[PASS] After a fast-forward merge, the content hash is unchanged.

    A verdict written against feature-branch hash H survives on main because
    project_decision_status keys compliance lookups on content_hash, not SHA.

    Invariants:
    - post-merge link_commit on main finds existing verdict → decisions_reflected >= 1
    - decision.status remains reflected (no pending re-check needed)
    """
    repo = _eph_repo

    # Build verdict on feature branch.
    _checkout(repo, "feat/ff", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.12\n"
    )
    _commit(repo, "set rate 12%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Pricing", intent="Apply rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted

    # Fast-forward merge to main.
    _checkout(repo, "main")
    _merge(repo, "feat/ff")  # FF: no divergence on main since seed

    invalidate_sync_cache(ctx)
    lc_main = await handle_link_commit(ctx, "HEAD")

    # Existing verdict for the same hash → status stays reflected,
    # no new compliance check needed.
    post_status = await _get_decision_status(ctx, decision_id)
    assert post_status == "reflected", (
        f"post-FF-merge status should stay reflected, got {post_status}"
    )
    # No new pending compliance check for this decision (verdict already exists).
    new_pending = [p for p in lc_main.pending_compliance_checks if p.decision_id == decision_id]
    assert not new_pending, (
        f"Should not re-pend after FF merge with same hash, got: {new_pending}"
    )


# ── E4: Squash merge → same content hash → reflected ──────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e04_squash_merge_verdict_survives(_eph_repo):
    """[PASS] Squash merge produces the same content → same hash → verdict carries over.

    Invariants:
    - squash merge commit has same calc.py content as feature branch
    - content_hash is identical → compliance verdict lookup succeeds
    - decision.status remains reflected
    """
    repo = _eph_repo

    _checkout(repo, "feat/squash", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.18\n"
    )
    _commit(repo, "set rate 18%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate policy", intent="Set 18% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted

    # Squash merge back to main — same content, new commit SHA.
    _checkout(repo, "main")
    _merge(repo, "feat/squash", squash=True)

    invalidate_sync_cache(ctx)
    lc_main = await handle_link_commit(ctx, "HEAD")

    post_status = await _get_decision_status(ctx, decision_id)
    assert post_status == "reflected", (
        f"post-squash-merge status should be reflected, got {post_status}"
    )
    new_pending = [p for p in lc_main.pending_compliance_checks if p.decision_id == decision_id]
    assert not new_pending, (
        f"No re-pend needed after squash merge with identical content, got: {new_pending}"
    )


# ── E5: Content change → drifted (prior compliant verdict exists) ─────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e05_content_change_becomes_drifted(_eph_repo):
    """[PASS] After a content change, a decision with a prior compliant verdict
    transitions to 'drifted' (not 'pending'), because has_prior_compliant_verdict
    returns True.

    Invariants:
    - Before change: reflected
    - After change: status = drifted (prior compliant exists for old hash)
    - A new pending_compliance_check is surfaced for the new hash
    """
    repo = _eph_repo
    ctx = BicameralContext.from_env()

    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="10% discount rule", intent="Apply 10% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc1 = await handle_link_commit(ctx, "HEAD")
    rc = await _resolve_verdict(ctx, lc1, decision_id)
    assert rc.accepted

    status_before = await _get_decision_status(ctx, decision_id)
    assert status_before == "reflected", f"Expected reflected before change, got {status_before}"

    # Change the file content → new hash.
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.25\n"
    )
    _commit(repo, "bump rate to 25%")

    lc2 = await handle_link_commit(ctx, "HEAD")

    # A new compliance check must be surfaced for the new hash.
    new_pending = [p for p in lc2.pending_compliance_checks if p.decision_id == decision_id]
    assert new_pending, "Expected new pending check after content change"

    status_after = await _get_decision_status(ctx, decision_id)
    assert status_after == "drifted", (
        f"Expected drifted after content change (prior compliant exists), got {status_after}"
    )


# ── E6: Branch switch → stale ephemeral not cleared [xfail V2] ────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e06_branch_switch_stale_not_cleared(_eph_repo):
    """[PASS V2] Switching to a diverged feature branch surfaces stale verdict as 'drifted'.

    V2 removes the is_authoritative gate from update_region_hash and
    project_decision_status, so on feature-B:
    - code_region.content_hash is updated to H_B (rate 25%)
    - project_decision_status: no verdict for H_B, but prior compliant (H_A) → 'drifted'

    This replaces the stale 'reflected' that V1 returned (verdict looked up by H_A,
    which was still stored in code_region.content_hash after skipping update_region_hash).

    Invariants:
    - feature-A: reflected (H_A = 15%)
    - switch to diverged feature-B (H_B = 25%)
    - link_commit on feature-B → status = 'drifted' (not stale 'reflected')
    """
    repo = _eph_repo

    # feature-A: establish 'reflected' for rate 15%.
    _checkout(repo, "feat/branch-a", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.15\n"
    )
    _commit(repo, "branch-A: rate 15%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate policy", intent="Apply 15% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc_a = await handle_link_commit(ctx, "HEAD")
    rc = await _resolve_verdict(ctx, lc_a, decision_id)
    assert rc.accepted
    assert await _get_decision_status(ctx, decision_id) == "reflected"

    # Switch to feature-B (diverged from main, not from feature-A).
    _checkout(repo, "main")
    _checkout(repo, "feat/branch-b", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.25\n"
    )
    _commit(repo, "branch-B: rate 25%")

    invalidate_sync_cache(ctx)
    lc_b = await handle_link_commit(ctx, "HEAD")

    # V2: update_region_hash now unconditional → H_B stored.
    # project_decision_status: no verdict for H_B, prior compliant (H_A) → 'drifted'.
    status_on_b = await _get_decision_status(ctx, decision_id)
    assert status_on_b == "drifted", (
        f"After switching to diverged feature-B, status must be 'drifted' "
        f"(stale 'reflected' from feature-A cleared), got {status_on_b}"
    )


# ── E7: Feature → main after merge → ephemeral not promoted [xfail V2] ────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e07_feature_to_main_ephemeral_not_promoted(_eph_repo):
    """[PASS V2] After FF-merge, compliance_check.ephemeral is promoted from True to False.

    V2: promote_ephemeral_verdict called in ingest_commit when is_authoritative=True
    flips the stored ephemeral=True row to False when the hash lands on main.
    """
    repo = _eph_repo

    _checkout(repo, "feat/promote", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.11\n"
    )
    _commit(repo, "rate 11%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate", intent="11% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    await _resolve_verdict(ctx, lc, decision_id)

    # Merge to main — same content, same hash.
    _checkout(repo, "main")
    _merge(repo, "feat/promote")
    invalidate_sync_cache(ctx)
    await handle_link_commit(ctx, "HEAD")

    # V2 expectation: ephemeral should now be False (hash is on main).
    checks = await _get_compliance_checks(ctx, decision_id)
    assert checks, "no compliance check found"
    # This assertion fails in V1 — ephemeral stays True even after merge.
    assert checks[0]["ephemeral"] is False, (
        "V2 gap: compliance_check.ephemeral must be promoted to False after "
        "the same hash lands on the authoritative branch"
    )


# ── E8: Detached HEAD → non-ephemeral (safe default) ─────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e08_detached_head_non_ephemeral(_eph_repo):
    """[PASS] Detached HEAD is treated as non-ephemeral (safe default).

    When `git rev-parse --abbrev-ref HEAD` returns "HEAD" (detached state),
    ingest_commit treats it as authoritative (no branch guard fires) and
    _is_ephemeral_commit returns False because `git merge-base --is-ancestor`
    exits 0 (the detached HEAD was created from main's HEAD).

    Invariants:
    - link_commit.ephemeral is False
    - ingest_commit writes baseline (is_authoritative=True)
    - full cycle still produces reflected
    """
    repo = _eph_repo

    # Detach HEAD at main's current tip.
    head_sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "--detach", head_sha)

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate", intent="Rate policy",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    assert lc.ephemeral is False, (
        f"Detached HEAD must be non-ephemeral (safe default), got ephemeral={lc.ephemeral}"
    )
    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", f"Expected reflected in detached HEAD, got {status}"


# ── E9: Process restart → flag lost, status still correct ─────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e09_process_restart_flag_lost_status_ok(_eph_repo):
    """[PASS] After a simulated process restart, _sync_state is empty.

    A resolve_compliance call made with a fresh ctx (no pending_flow_id in
    _sync_state) logs a warning and stores ephemeral=False (safe default).
    Status projection still works because update_region_hash + project_decision_status
    run unconditionally.

    Invariants:
    - resolve_compliance without matching flow_id → ephemeral=False (warning)
    - decision.status still transitions to reflected
    """
    repo = _eph_repo

    # Set up feature branch.
    _checkout(repo, "feat/restart", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.13\n"
    )
    _commit(repo, "rate 13%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate", intent="13% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    assert lc.ephemeral is True

    pending = [p for p in lc.pending_compliance_checks if p.decision_id == decision_id]
    assert pending, "Expected pending check"

    # Simulate process restart: fresh context with empty _sync_state.
    ctx2 = BicameralContext.from_env()
    assert not ctx2._sync_state.get("pending_flow_id"), (
        "Fresh ctx must have no pending_flow_id (process restart)"
    )

    # resolve_compliance on ctx2 without flow_id — ephemeral defaults to False.
    rc = await handle_resolve_compliance(
        ctx2,
        phase="ingest",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": pending[0].region_id,
            "content_hash": pending[0].content_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "post-restart",
        }],
        # No flow_id — simulating process restart
    )
    assert rc.accepted, f"resolve rejected post-restart: {rc.rejected}"

    status = await _get_decision_status(ctx2, decision_id)
    assert status == "reflected", (
        f"Status must be reflected after restart, got {status}"
    )

    checks = await _get_compliance_checks(ctx2, decision_id)
    assert checks
    # Flag lost: ephemeral=False even though verdict was from a feature branch.
    assert checks[0]["ephemeral"] is False or checks[0]["ephemeral"] == False, (
        "Post-restart: ephemeral=False because flag was not in fresh _sync_state"
    )


# ── E10: Idempotent resolve_compliance ────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e10_idempotent_resolve_compliance(_eph_repo):
    """[PASS] Calling resolve_compliance twice for the same (d,r,h) is idempotent.

    The first CREATE wins (UNIQUE index on decision_id, region_id, content_hash).
    The second call silently no-ops (CREATE catches 'already contains', returns False).
    Status remains reflected after both calls.

    Invariants:
    - second resolve returns accepted (not rejected)
    - exactly one compliance_check row exists (not two)
    - status still reflected
    """
    repo = _eph_repo
    ctx = BicameralContext.from_env()

    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Discount rate", intent="Apply rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    pending = [p for p in lc.pending_compliance_checks if p.decision_id == decision_id]
    assert pending

    verdict_payload = [{
        "decision_id": decision_id,
        "region_id": pending[0].region_id,
        "content_hash": pending[0].content_hash,
        "verdict": "compliant",
        "confidence": "high",
        "explanation": "first call",
    }]

    rc1 = await handle_resolve_compliance(ctx, phase="ingest", verdicts=verdict_payload, flow_id=lc.flow_id)
    assert rc1.accepted

    # Second call with same payload — must succeed silently.
    rc2 = await handle_resolve_compliance(ctx, phase="ingest", verdicts=verdict_payload)
    assert rc2.accepted, f"Second idempotent call rejected: {rc2.rejected}"

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected"

    # Only one compliance_check row despite two calls.
    checks = await _get_compliance_checks(ctx, decision_id)
    assert len(checks) == 1, (
        f"Expected exactly 1 compliance_check (UNIQUE constraint), got {len(checks)}"
    )


# ── E11: flow_id mismatch → ephemeral=False, status correct ───────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e11_flow_id_mismatch_ephemeral_false_status_ok(_eph_repo):
    """[PASS] A mismatched flow_id causes ephemeral to default to False,
    but does not block the compliance write or status projection.

    Invariants:
    - resolve_compliance with stale/wrong flow_id still returns accepted
    - compliance_check.ephemeral=False (flag not trusted from mismatched call)
    - decision.status = reflected
    """
    repo = _eph_repo

    _checkout(repo, "feat/flow", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.14\n"
    )
    _commit(repo, "rate 14%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 14%", intent="Apply 14% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    assert lc.ephemeral is True

    pending = [p for p in lc.pending_compliance_checks if p.decision_id == decision_id]
    assert pending

    stale_flow_id = "00000000-0000-0000-0000-000000000000"
    rc = await handle_resolve_compliance(
        ctx,
        phase="ingest",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": pending[0].region_id,
            "content_hash": pending[0].content_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "stale flow",
        }],
        flow_id=stale_flow_id,
    )
    assert rc.accepted, f"Expected accepted despite flow_id mismatch, got: {rc.rejected}"

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", f"Expected reflected, got {status}"

    checks = await _get_compliance_checks(ctx, decision_id)
    assert checks
    assert checks[0]["ephemeral"] is False or checks[0]["ephemeral"] == False, (
        "Mismatched flow_id: ephemeral must default to False"
    )


# ── E12: First feature-branch sync, no cursor → head_only [xfail V2] ──────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e12_feature_branch_reflected_drift_not_detected(_eph_repo):
    """[PASS V2] Branch-delta sweep detects calc.py drift even when HEAD commit is helper.py.

    V2 adds `git diff <auth>...HEAD --name-only` sweep after the head-only sweep.
    This covers all files changed on the branch since the merge base, catching
    calc.py (from commit 2) even though HEAD is the helper.py commit.

    Setup:
      - Commit 1: change calc.py → verify → 'reflected' for H_20%
      - Commit 2: change calc.py to H_30% (drift vs reflected verdict)
      - Commit 3: add helper.py (becomes HEAD — different file)
      - link_commit(HEAD, no prior cursor): branch-delta adds calc.py → drift detected
    """
    repo = _eph_repo

    # Establish feature branch from main seed.
    _checkout(repo, "feat/silent-drift", create=True)

    # Feature commit 1: change calc.py — becomes the "feature hash" we'll verify.
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.20\n"
    )
    _commit(repo, "commit 1: rate to 20% (feature version)")

    ctx = BicameralContext.from_env()

    # Ingest + bind on feature branch. The ingest's internal link_commit runs
    # with head-only scope (no cursor). Since HEAD = commit 1 (calc.py),
    # calc.py IS in changed_files → pending check surfaced → we can verify it.
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 20%", intent="Rate policy",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc1 = await handle_link_commit(ctx, "HEAD")
    rc = await _resolve_verdict(ctx, lc1, decision_id)
    assert rc.accepted
    # Decision is now 'reflected' for the 20% hash.
    assert await _get_decision_status(ctx, decision_id) == "reflected"

    # Feature commit 2: change calc.py AGAIN (drift vs the reflected verdict).
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.30\n"
    )
    _commit(repo, "commit 2: rate to 30% — now drifted from reflected verdict")

    # Feature commit 3: add an unrelated file (becomes HEAD).
    (repo / "src/helper.py").write_text("def noop(): pass\n")
    _commit(repo, "commit 3: add helper.py — HEAD now")

    invalidate_sync_cache(ctx)
    lc2 = await handle_link_commit(ctx, "HEAD")

    # V2 expectation: calc.py drift must be detected (rate now 30%, not 20%).
    # V1 gap: HEAD = helper.py commit, no prior cursor on this branch path →
    #   head-only sweep only sees helper.py, decision is 'reflected' → stale-pending
    #   sweep skips it → drift silently missed.
    new_pending = [p for p in lc2.pending_compliance_checks if p.decision_id == decision_id]
    assert new_pending, (
        "V2 gap: branch-delta sweep must detect calc.py drift (30% ≠ 20%) even when "
        f"HEAD commit is helper.py. sweep_scope={lc2.sweep_scope}, "
        f"pending_checks={[(p.file_path, p.content_hash[:8]) for p in lc2.pending_compliance_checks]}"
    )


# ── E13: Rebase → same content hash → verdict carries over ────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e13_rebase_same_hash_verdict_survives(_eph_repo):
    """[PASS] After rebase, the content is identical so the hash is unchanged.

    Compliance verdicts are keyed on content_hash, not commit SHA. Rebase
    creates a new SHA but the same content → same hash → verdict found.

    Invariants:
    - pre-rebase: verdict written, status=reflected
    - post-rebase: same content_hash → verdict found → status stays reflected
    - no new pending_compliance_check surfaced for this decision
    """
    repo = _eph_repo

    # Build feature branch (no conflict with main's seed).
    _checkout(repo, "feat/rebase-me", create=True)
    # Append a new function (no conflict with rate()).
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.1\n\n"
        "def tax(amount: float) -> float:\n    return amount * 0.07\n"
    )
    _commit(repo, "add tax()")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Tax calc", intent="Compute 7% tax",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "tax",
                     "start_line": 4, "end_line": 5,
                     "type": "function", "purpose": "tax",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc1 = await handle_link_commit(ctx, "HEAD")
    rc = await _resolve_verdict(ctx, lc1, decision_id)
    assert rc.accepted

    pre_rebase_status = await _get_decision_status(ctx, decision_id)
    assert pre_rebase_status == "reflected"

    # Add a diverging commit on main so rebase actually rewrites SHA.
    _checkout(repo, "main")
    (repo / "src/utils.py").write_text("# utility module\n")
    _commit(repo, "add utils.py on main")

    # Rebase feature branch onto updated main.
    _checkout(repo, "feat/rebase-me")
    try:
        _git(repo, "rebase", "main")
    except subprocess.CalledProcessError:
        # Resolve any conflicts by keeping the feature version.
        _git(repo, "checkout", "--ours", "src/calc.py")
        _git(repo, "add", "src/calc.py")
        _git(repo, "rebase", "--continue")

    invalidate_sync_cache(ctx)
    lc2 = await handle_link_commit(ctx, "HEAD")

    # Same content_hash → verdict found → status stays reflected.
    post_rebase_status = await _get_decision_status(ctx, decision_id)
    assert post_rebase_status == "reflected", (
        f"Post-rebase status should stay reflected (same hash), got {post_rebase_status}"
    )
    new_pending = [p for p in lc2.pending_compliance_checks if p.decision_id == decision_id]
    assert not new_pending, (
        f"No re-pend expected after rebase with same content, got: {new_pending}"
    )


# ── E14: Deleted branch → verdict survives (hash-keyed) ───────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e14_deleted_branch_verdict_survives(_eph_repo):
    """[PASS] Compliance verdicts persist after the source branch is deleted.

    Verdicts are stored as compliance_check rows keyed on content_hash. They
    have no dependency on the branch that produced them. Deleting the branch
    does not remove the verdict.

    Invariants:
    - verdict written on feature branch, branch deleted
    - resolve_compliance on main for same hash returns accepted (idempotent)
    - status = reflected
    """
    repo = _eph_repo

    _checkout(repo, "feat/doomed", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.16\n"
    )
    _commit(repo, "rate 16%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 16%", intent="16% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")
    pending = [p for p in lc.pending_compliance_checks if p.decision_id == decision_id]
    assert pending
    feature_hash = pending[0].content_hash

    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted

    # Delete the branch.
    _checkout(repo, "main")
    _git(repo, "branch", "-D", "feat/doomed")

    # On main, FF-merge the content (pretend it landed).
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.16\n"
    )
    _commit(repo, "adopt 16% rate on main")

    invalidate_sync_cache(ctx)
    lc_main = await handle_link_commit(ctx, "HEAD")

    # Same content → same hash → existing verdict found.
    post_status = await _get_decision_status(ctx, decision_id)
    assert post_status == "reflected", (
        f"Verdict should survive branch deletion (hash-keyed), got {post_status}"
    )


# ── E15: authoritative_ref="" → degraded safe mode ────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e15_custom_authoritative_ref_non_ephemeral(_eph_repo, monkeypatch):
    """[PASS] When BICAMERAL_AUTHORITATIVE_REF is set to a custom branch (e.g. "develop"),
    commits on that branch are non-ephemeral, and the full cycle produces reflected.

    Covers users whose primary integration branch is not "main" (e.g. "develop",
    "trunk"). Demonstrates that the ephemeral check is branch-name-aware: commits
    reachable from the designated authoritative ref are non-ephemeral, others are.

    Invariants:
    - link_commit.ephemeral is False when current branch == BICAMERAL_AUTHORITATIVE_REF
    - Full cycle (ingest → link_commit → resolve_compliance) produces reflected
    - A separate side-branch off this custom auth ref IS ephemeral
    """
    repo = _eph_repo

    # Create "develop" as the custom authoritative branch from main's seed.
    _checkout(repo, "develop", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.19\n"
    )
    _commit(repo, "develop: rate 19%")

    # Override to use "develop" as the authoritative ref.
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "develop")

    ctx = BicameralContext.from_env()
    assert ctx.authoritative_ref == "develop", (
        f"Expected authoritative_ref=develop, got {ctx.authoritative_ref}"
    )

    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 19%", intent="19% rate on develop",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc = await handle_link_commit(ctx, "HEAD")

    assert lc.ephemeral is False, (
        f"Commits on develop (= authoritative_ref) must be non-ephemeral, got {lc.ephemeral}"
    )

    rc = await _resolve_verdict(ctx, lc, decision_id)
    assert rc.accepted

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", f"Expected reflected on develop, got {status}"

    # Side branch off develop IS ephemeral.
    _checkout(repo, "feat/off-develop", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.21\n"
    )
    _commit(repo, "side branch: rate 21%")

    invalidate_sync_cache(ctx)
    lc_side = await handle_link_commit(ctx, "HEAD")
    assert lc_side.ephemeral is True, (
        f"Side branch off develop must be ephemeral, got {lc_side.ephemeral}"
    )


# ── E16: resolve_compliance without prior link_commit ─────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e16_resolve_compliance_without_link_commit(_eph_repo):
    """[PASS] resolve_compliance can be called without a prior link_commit.

    The handler calls update_region_hash + project_decision_status unconditionally,
    so status projection works even if link_commit never ran for this hash.

    Invariants:
    - ingest + bind (no link_commit)
    - resolve_compliance directly with bind's content_hash
    - decision.status = reflected
    """
    repo = _eph_repo
    ctx = BicameralContext.from_env()

    decision_id, region_id, bind_hash = await _ingest_and_bind(
        ctx, repo,
        intent="Direct resolve no link_commit",
        file_path="src/calc.py",
        symbol_name="rate",
        start_line=1, end_line=2,
    )

    # Call resolve_compliance directly (no link_commit, no flow_id).
    rc = await handle_resolve_compliance(
        ctx,
        phase="ingest",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": region_id,
            "content_hash": bind_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "direct resolve",
        }],
    )
    assert rc.accepted, f"Direct resolve rejected: {rc.rejected}"

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", (
        f"Expected reflected after direct resolve_compliance, got {status}"
    )


# ── E17: Ephemeral first-write-wins blocks non-ephemeral flag [xfail V2] ──────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e17_ephemeral_first_write_wins_flag_stuck(_eph_repo):
    """[PASS V2] promote_ephemeral_verdict flips ephemeral=True to False on main confirmation.

    V2: resolve_compliance calls promote_ephemeral_verdict before upsert_compliance_check
    when is_ephemeral=False. The UPDATE fires before the CREATE no-ops, flipping the
    existing ephemeral=True row to False.
    """
    repo = _eph_repo

    # Feature branch: write ephemeral=True verdict.
    _checkout(repo, "feat/first-write", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.17\n"
    )
    _commit(repo, "rate 17%")

    ctx = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 17%", intent="17% rate",
                 code_regions=[{
                     "file_path": "src/calc.py",
                     "symbol": "rate",
                     "start_line": 1, "end_line": 2,
                     "type": "function", "purpose": "rate",
                 }]),
    )
    decision_id = ingest.created_decisions[0].decision_id
    lc_feat = await handle_link_commit(ctx, "HEAD")
    assert lc_feat.ephemeral is True
    pending = [p for p in lc_feat.pending_compliance_checks if p.decision_id == decision_id]
    assert pending
    feature_hash = pending[0].content_hash

    rc_feat = await _resolve_verdict(ctx, lc_feat, decision_id)
    assert rc_feat.accepted

    # Verify: first write was ephemeral=True.
    checks_after_feat = await _get_compliance_checks(ctx, decision_id)
    assert checks_after_feat[0]["ephemeral"] is True or checks_after_feat[0]["ephemeral"] == True

    # Merge same content to main.
    _checkout(repo, "main")
    _merge(repo, "feat/first-write")
    invalidate_sync_cache(ctx)
    await handle_link_commit(ctx, "HEAD")

    # Now try to "confirm" on main (same hash, non-ephemeral).
    rc_main = await handle_resolve_compliance(
        ctx,
        phase="drift",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": pending[0].region_id,
            "content_hash": feature_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "main confirmation",
        }],
        # No flow_id — ctx is on main, no pending_ephemeral in sync_state
    )
    assert rc_main.accepted

    # V2 expectation: ephemeral should be flipped to False.
    checks_final = await _get_compliance_checks(ctx, decision_id)
    assert checks_final, "no compliance check"
    # This assertion FAILS in V1 — record stays ephemeral=True (first write wins).
    assert checks_final[0]["ephemeral"] is False, (
        "V2 gap: ephemeral=True record must be updated to False when the "
        "same hash is confirmed on the authoritative branch"
    )


# ── E18: Bind against a branch-local file succeeds (ephemeral-aware ref) ────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e18_bind_branch_local_file(_eph_repo):
    """[PASS] bicameral_bind succeeds for a file that only exists on the feature branch.

    Bug: bind was always validating file/symbol existence at authoritative_sha (main).
    Files added on a feature branch don't exist at main's SHA, so bind rejected them.
    Fix: when _is_ephemeral_commit returns True, use head_sha as the effective ref.

    Invariants:
    - feature branch adds NewModule.py (not on main)
    - bind for a symbol in NewModule.py succeeds (no error)
    - resulting content_hash is non-empty (hashed from branch content)
    - decision status transitions to pending (region created)
    """
    repo = _eph_repo

    _checkout(repo, "feat/new-module", create=True)
    (repo / "src/new_module.py").write_text(
        "def compute(x: int) -> int:\n    return x * 2\n"
    )
    _commit(repo, "add new_module.py (branch-only file)")

    ctx = BicameralContext.from_env()

    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Double computation", intent="Double the input value"),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id

    bind_resp = await handle_bind(ctx, [{
        "decision_id": decision_id,
        "file_path": "src/new_module.py",
        "symbol_name": "compute",
        "start_line": 1,
        "end_line": 2,
    }])

    assert bind_resp.bindings, "no bind results"
    b = bind_resp.bindings[0]
    assert not b.error, (
        f"bind must succeed for a branch-local file; got error: {b.error}"
    )
    assert b.content_hash, "content_hash must be non-empty after successful bind"


# ── E19: Bind to modified function uses branch hash, not main hash ────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e19_bind_modified_function_uses_branch_hash(_eph_repo):
    """[PASS] bind content_hash reflects branch content, not authoritative_sha content.

    Bug: _do_bind always computed content_hash at authoritative_sha (main HEAD).
    For a function that exists on both branches but with different content, bind
    returned H_main — the hash of main's version — even when called on a feature
    branch with different content.

    Fix: when _is_ephemeral_commit is True, effective_ref = head_sha.
    bind_result.content_hash is now computed at head_sha (branch content).

    Invariants:
    - main: rate() returns 0.1   → H_main
    - feature branch: rate() returns 0.25 → H_branch  (H_branch ≠ H_main)
    - bind on feature branch → bind_result.content_hash == H_branch
    - bind_result.content_hash != H_main
    """
    from ledger.status import compute_content_hash

    repo = _eph_repo

    # Capture H_main before branching.
    main_sha = _git(repo, "rev-parse", "HEAD")
    h_main = compute_content_hash("src/calc.py", 1, 2, str(repo), ref=main_sha)
    assert h_main, "pre-condition: main hash must be computable"

    # Create feature branch with different content.
    _checkout(repo, "feat/rate-change", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.25\n"
    )
    _commit(repo, "set rate to 25%")

    ctx = BicameralContext.from_env()

    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 25%", intent="Apply 25% rate to order total"),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id

    bind_resp = await handle_bind(ctx, [{
        "decision_id": decision_id,
        "file_path": "src/calc.py",
        "symbol_name": "rate",
        "start_line": 1,
        "end_line": 2,
    }])

    assert bind_resp.bindings, "no bind results"
    b = bind_resp.bindings[0]
    assert not b.error, f"bind failed: {b.error}"
    assert b.content_hash, "content_hash must be non-empty"

    assert b.content_hash != h_main, (
        f"bind content_hash must be branch hash (H_branch), not main hash (H_main={h_main[:8]}). "
        f"Got {b.content_hash[:8]} — suggests effective_ref was not updated to head_sha"
    )


# ── E20: Bind+link_commit hash consistency — no phantom drifted ──────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e20_bind_link_commit_hash_consistency_no_phantom_drift(_eph_repo):
    """[PASS] After bind on feature branch → resolve_compliance → second link_commit
    → status is 'reflected', not phantom 'drifted'.

    Root cause of phantom 'drifted' (pre-fix):
      1. bind used authoritative_sha → stored code_region.content_hash = H_main
      2. link_commit(HEAD) computed actual_hash = H_branch (≠ H_main)
      3. resolve_compliance stored compliance_check.content_hash = H_branch
      4. Second link_commit: stored_hash = H_main, actual_hash = H_branch → mismatch
         has_prior_compliant_verdict = True (H_branch verdict exists) → 'drifted'!

    With the fix:
      1. bind uses head_sha → stored code_region.content_hash = H_branch
      2. link_commit(HEAD): actual_hash = H_branch = stored_hash → no mismatch
      3. resolve_compliance stores verdict at H_branch
      4. Second link_commit: stored_hash = H_branch, actual_hash = H_branch
         verdict found at H_branch → 'reflected' ✓

    Invariants:
    - bind on feature branch with modified content
    - link_commit → pending_check at H_branch
    - resolve_compliance(H_branch, compliant)
    - second link_commit → status = 'reflected' (not 'drifted')
    - pending_check.content_hash == bind_result.content_hash (hash consistency)
    """
    repo = _eph_repo

    # Feature branch: modify rate().
    _checkout(repo, "feat/rate-v2", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n    return order_total * 0.26\n"
    )
    _commit(repo, "set rate to 26%")

    ctx = BicameralContext.from_env()

    ingest = await handle_ingest(
        ctx,
        _payload(repo, text="Rate 26%", intent="Apply 26% rate"),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id

    bind_resp = await handle_bind(ctx, [{
        "decision_id": decision_id,
        "file_path": "src/calc.py",
        "symbol_name": "rate",
        "start_line": 1,
        "end_line": 2,
    }])
    assert bind_resp.bindings and not bind_resp.bindings[0].error
    bind_hash = bind_resp.bindings[0].content_hash
    assert bind_hash, "bind must return content_hash"

    # Invalidate the session sync cache: ingest called link_commit internally
    # and cached the response (pre-bind, before the region was created).
    # Without this, the next link_commit call returns the stale cached response
    # that has no pending checks for the newly bound region.
    invalidate_sync_cache(ctx)

    # First link_commit: surfaces pending check at H_branch.
    lc1 = await handle_link_commit(ctx, "HEAD")
    pending = [p for p in lc1.pending_compliance_checks if p.decision_id == decision_id]
    assert pending, f"link_commit must surface pending check for the bound decision"
    assert pending[0].content_hash == bind_hash, (
        f"pending_check.content_hash ({pending[0].content_hash[:8]}) must equal "
        f"bind_result.content_hash ({bind_hash[:8]}) — hash consistency invariant"
    )

    # Resolve the compliance check.
    rc = await handle_resolve_compliance(
        ctx,
        phase="ingest",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": pending[0].region_id,
            "content_hash": pending[0].content_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "branch content verified",
        }],
        flow_id=lc1.flow_id,
    )
    assert rc.accepted, f"resolve_compliance rejected: {rc.rejected}"

    # Second link_commit: no new pending check, status should be reflected.
    invalidate_sync_cache(ctx)
    lc2 = await handle_link_commit(ctx, "HEAD")

    status = await _get_decision_status(ctx, decision_id)
    assert status == "reflected", (
        f"After resolve_compliance on feature branch, status must be 'reflected' "
        f"(not phantom 'drifted'). Got: {status}. "
        f"This fails pre-fix: bind stored H_main, link_commit computed H_branch → "
        f"mismatch + prior verdict → 'drifted' forever."
    )

    new_pending = [p for p in lc2.pending_compliance_checks if p.decision_id == decision_id]
    assert not new_pending, (
        f"No re-pend expected after compliant verdict for current hash, got: {new_pending}"
    )


# ── E21: Ungrounded → feature branch bind → reflected + ephemeral=True ────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e21_ungrounded_feature_bind_reflected_ephemeral(_eph_repo):
    """[PASS] Full flow: ungrounded decision → feature branch bind → reflected, ephemeral=True.

    This is the canonical "implement a decision on a feature branch" scenario.
    Starts with a decision that has no code binding (ungrounded), then:
      - engineer creates a feature branch and writes the implementation
      - calls bind against the new code (head_sha, not authoritative_sha)
      - link_commit + resolve_compliance on the feature branch
      - decision is 'reflected' and compliance_check.ephemeral=True

    Each tool call creates a fresh BicameralContext (matching real MCP server behavior).
    ctx.head_sha must reflect the current HEAD — stale head_sha causes bind to fall
    back to authoritative_sha and hash the wrong content.

    Invariants:
    - ingest without code_regions → status = 'ungrounded'
    - after feature branch bind → status = 'pending'
    - after link_commit + resolve_compliance → status = 'reflected'
    - compliance_check.ephemeral == True (verdict was on a feature branch)
    - lc.ephemeral == True (commit is not reachable from main)
    """
    repo = _eph_repo

    # Ingest on main — no code regions, starts ungrounded.
    ctx_main = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx_main,
        _payload(repo, text="Cap discount at 30%", intent="Discount must never exceed 30%"),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id
    assert await _get_decision_status(ctx_main, decision_id) == "ungrounded", (
        "Decision must start ungrounded when no code_regions are provided"
    )

    # Engineer creates feature branch and writes the implementation.
    _checkout(repo, "feat/cap-discount", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n"
        "    return min(order_total * 0.30, order_total)\n"
    )
    _commit(repo, "cap discount at 30% (feat/cap-discount)")

    # Fresh ctx on the feature branch — head_sha now points to the feature commit.
    # In the real MCP server, each tool call creates a new context from env, so
    # head_sha always reflects the current HEAD at call time.
    ctx_feat = BicameralContext.from_env()
    assert ctx_feat.head_sha != ctx_main.head_sha, (
        "ctx_feat must have a different head_sha than ctx_main (branch diverged)"
    )

    # Bind to the implementation on the feature branch.
    bind_resp = await handle_bind(ctx_feat, [{
        "decision_id": decision_id,
        "file_path": "src/calc.py",
        "symbol_name": "rate",
        "start_line": 1,
        "end_line": 2,
    }])
    assert bind_resp.bindings and not bind_resp.bindings[0].error, (
        f"bind must succeed on feature branch: "
        f"{bind_resp.bindings[0].error if bind_resp.bindings else 'no results'}"
    )
    assert await _get_decision_status(ctx_feat, decision_id) == "pending", (
        "Status must be 'pending' after bind (region created, no verdict yet)"
    )

    # link_commit: feature branch commit is not reachable from main → ephemeral=True.
    lc = await handle_link_commit(ctx_feat, "HEAD")
    assert lc.ephemeral is True, (
        f"link_commit on feature branch must be ephemeral=True, got {lc.ephemeral}"
    )

    pending = [p for p in lc.pending_compliance_checks if p.decision_id == decision_id]
    assert pending, "link_commit must surface a pending compliance check for the bound decision"

    # Resolve: LLM verifies the implementation is compliant.
    rc = await handle_resolve_compliance(
        ctx_feat,
        phase="ingest",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": pending[0].region_id,
            "content_hash": pending[0].content_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "cap implementation verified",
        }],
        flow_id=lc.flow_id,
    )
    assert rc.accepted, f"resolve_compliance rejected: {rc.rejected}"

    # Final assertions: reflected AND ephemeral.
    status = await _get_decision_status(ctx_feat, decision_id)
    assert status == "reflected", (
        f"Status must be 'reflected' after compliant verdict on feature branch, got {status}"
    )

    checks = await _get_compliance_checks(ctx_feat, decision_id)
    assert checks, "compliance_check row must exist"
    assert checks[0]["ephemeral"] is True, (
        f"compliance_check.ephemeral must be True (verdict was on a feature branch), "
        f"got {checks[0]['ephemeral']}"
    )


# ── E22: Switch back to main — no stale ephemeral 'reflected' ────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_e22_switch_to_main_no_stale_reflected(_eph_repo):
    """[PASS] After switching back to main (no merge), status is 'drifted', not stale 'reflected'.

    The feature branch produced: status='reflected', compliance_check.ephemeral=True.
    On main the implementation doesn't exist yet (H_main ≠ H_branch).
    After link_commit on main (fresh ctx):
      - code_region.content_hash is updated to H_main (authoritative path)
      - project_decision_status: no verdict at H_main, but prior compliant verdict
        exists (H_branch, ephemeral) → 'drifted'

    This confirms the ephemeral verdict does NOT falsely promote main to 'reflected'.
    'drifted' is the correct signal: "a verified implementation exists on a branch,
    but main doesn't have it yet — main's code is out of sync with the decision."

    Each branch switch creates a fresh BicameralContext (matching real MCP behavior),
    so head_sha correctly reflects the current HEAD on each branch.

    Invariants:
    - feature branch: status = 'reflected', ephemeral=True
    - switch to main (no merge): link_commit on main → status = 'drifted'
    - status is NOT 'reflected' (stale ephemeral not carried over)
    - compliance_check.ephemeral remains True (the row itself is unchanged)
    """
    repo = _eph_repo

    # Ingest on main — ungrounded.
    ctx_main = BicameralContext.from_env()
    ingest = await handle_ingest(
        ctx_main,
        _payload(repo, text="Max 30% cap", intent="Discount cap at 30%"),
    )
    assert ingest.ingested
    decision_id = ingest.created_decisions[0].decision_id

    # Feature branch: implement + bind + resolve → reflected, ephemeral=True.
    _checkout(repo, "feat/cap-v2", create=True)
    (repo / "src/calc.py").write_text(
        "def rate(order_total: float) -> float:\n"
        "    return min(order_total * 0.30, order_total)\n"
    )
    _commit(repo, "cap at 30%")

    # Fresh ctx on the feature branch.
    ctx_feat = BicameralContext.from_env()

    bind_resp = await handle_bind(ctx_feat, [{
        "decision_id": decision_id,
        "file_path": "src/calc.py",
        "symbol_name": "rate",
        "start_line": 1,
        "end_line": 2,
    }])
    assert bind_resp.bindings and not bind_resp.bindings[0].error

    lc_feat = await handle_link_commit(ctx_feat, "HEAD")
    assert lc_feat.ephemeral is True
    pending = [p for p in lc_feat.pending_compliance_checks if p.decision_id == decision_id]
    assert pending

    rc = await handle_resolve_compliance(
        ctx_feat,
        phase="ingest",
        verdicts=[{
            "decision_id": decision_id,
            "region_id": pending[0].region_id,
            "content_hash": pending[0].content_hash,
            "verdict": "compliant",
            "confidence": "high",
            "explanation": "verified on branch",
        }],
        flow_id=lc_feat.flow_id,
    )
    assert rc.accepted

    assert await _get_decision_status(ctx_feat, decision_id) == "reflected", (
        "Pre-condition: feature branch must show 'reflected' before switch"
    )

    # Switch back to main WITHOUT merging.
    # Fresh ctx — head_sha now points to main's HEAD again.
    _checkout(repo, "main")
    ctx_back = BicameralContext.from_env()
    lc_main = await handle_link_commit(ctx_back, "HEAD")

    status_on_main = await _get_decision_status(ctx_back, decision_id)

    assert status_on_main != "reflected", (
        "STALE EPHEMERAL BUG: main must NOT show 'reflected' from a feature branch "
        "ephemeral verdict — the implementation hasn't landed on main yet."
    )
    assert status_on_main == "drifted", (
        f"After switching to main without merging, status must be 'drifted' "
        f"(prior compliant verdict exists on branch, but H_main has no verdict). "
        f"Got: {status_on_main}"
    )

    # The compliance_check row itself still records ephemeral=True (it's unchanged).
    checks = await _get_compliance_checks(ctx_back, decision_id)
    assert checks, "compliance_check row must still exist"
    assert checks[0]["ephemeral"] is True, (
        f"compliance_check.ephemeral must still be True (row unchanged after branch switch), "
        f"got {checks[0]['ephemeral']}"
    )
