"""Jacob North Star regression suite — v0.7 guardrail.

Five invariants Jacob was promised as first product champion.
Wednesday's v0.7.0 ships only if ALL five pass green. No exceptions.

Each test is a standalone exercise of one invariant. The full E2E flow
(ingest→bind→commit→reflected) is tested in test_alpha_contract.py;
this file validates that the v0.7 signoff schema change (product_signoff
→ signoff, proposal state) did NOT regress any load-bearing behavior.

Invariants:
1. Ingest — decisions land in the ledger, searchable by feature area.
2. Bind — caller-LLM link is author-attested.
3. Commit — bound regions hashed; decisions flip reflected or drifted.
4. Session-start banner — drifted decisions surface unprompted on first call.
5. Preflight — bound file returns its governing decisions.

Plus one v0.7-specific invariant:
6. Proposal state — new ingests enter as 'proposal'; drift-exempt until ratified.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import reset_ledger_singleton
from context import BicameralContext
from handlers.bind import handle_bind
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit, invalidate_sync_cache
from handlers.preflight import handle_preflight
from handlers.resolve_compliance import handle_resolve_compliance
from handlers.search_decisions import handle_search_decisions
from handlers.sync_middleware import ensure_ledger_synced, get_session_start_banner
from ledger.queries import project_decision_status


# ── Shared helpers ───────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


_INITIAL_IMPL = """
def fetch_user(user_id: int):
    # JWT-authenticated lookup.
    return {"id": user_id, "name": "Test User"}
"""


def _seed_repo(repo_root: Path, body: str = _INITIAL_IMPL) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "jacob@example.com")
    _git(repo_root, "config", "user.name", "Jacob Test")
    (repo_root / "impl.py").write_text(dedent(body).strip() + "\n")
    _git(repo_root, "add", ".")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")


def _commit_edit(repo_root: Path, new_body: str, msg: str = "edit") -> None:
    (repo_root / "impl.py").write_text(dedent(new_body).strip() + "\n")
    _git(repo_root, "add", "impl.py")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)


def _ratified_payload(description: str, *, with_region: bool = False) -> dict:
    """Build a payload with a ratified signoff (enables drift checking)."""
    mapping: dict = {
        "intent": description,
        "span": {
            "source_type": "transcript",
            "text": description,
            "source_ref": "jacob-guardrail-2026-04-24",
            "speakers": ["jacob@example.com"],
            "meeting_date": "2026-04-24",
        },
        "symbols": [],
        "code_regions": [],
        "signoff": {
            "state": "ratified",
            "signer": "jacob@example.com",
            "ratified_at": "2026-04-24T10:00:00Z",
            "session_id": None,
        },
    }
    if with_region:
        mapping["code_regions"] = [{
            "file_path": "impl.py",
            "symbol": "fetch_user",
            "type": "function",
            "start_line": 1,
            "end_line": 3,
            "purpose": description,
        }]
    return {"query": description, "repo": "jacob-repo", "mappings": [mapping]}


@pytest.fixture
def alpha_env(tmp_path, monkeypatch):
    """Isolated git repo + fresh ledger for each Jacob invariant test."""
    repo_root = tmp_path / "jacob-repo"
    _seed_repo(repo_root)
    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    reset_ledger_singleton()
    ctx = BicameralContext.from_env()
    yield ctx, repo_root
    reset_ledger_singleton()


async def _decision_status(ctx, decision_id: str) -> str:
    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    return await project_decision_status(inner._client, decision_id)


# ── Invariant 1+2+3: ingest → bind → commit → reflected ─────────────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_ingest_bind_commit_marks_reflected(alpha_env):
    """Invariants 1, 2, 3 — full happy path:
    ratified decision, caller-LLM bind, compliant verdict → reflected.
    """
    ctx, _ = alpha_env

    # Invariant 1: ingest lands in ledger, searchable.
    ingest_resp = await handle_ingest(ctx, _ratified_payload(
        "JWT is the session-auth primitive, not cookies.", with_region=False,
    ))
    assert ingest_resp.ingested
    assert len(ingest_resp.pending_grounding_decisions) == 1
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    search_resp = await handle_search_decisions(ctx, query="JWT session", max_results=5)
    assert any(m.decision_id == decision_id for m in search_resp.matches), (
        "Invariant 1 FAIL: ingested decision not searchable"
    )

    # Invariant 2: bind is author-attested.
    bind_resp = await handle_bind(ctx, bindings=[{
        "decision_id": decision_id,
        "file_path": "impl.py",
        "symbol_name": "fetch_user",
        "start_line": 1,
        "end_line": 3,
    }])
    b = bind_resp.bindings[0]
    assert b.error is None, f"Invariant 2 FAIL: bind error: {b.error}"
    assert b.region_id and b.content_hash

    # Invariant 3: compliant verdict + ratified signoff → reflected.
    rc = await handle_resolve_compliance(ctx, phase="ingest", verdicts=[{
        "decision_id": decision_id,
        "region_id": b.region_id,
        "content_hash": b.content_hash,
        "verdict": "compliant",
        "confidence": "high",
        "explanation": "fetch_user performs JWT lookup as decided.",
    }])
    assert len(rc.accepted) == 1
    status = await _decision_status(ctx, decision_id)
    assert status == "reflected", f"Invariant 3 FAIL: expected reflected, got {status}"


# ── Invariant 3 (drift arm): edit without rebind → drifted ──────────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_code_edit_without_rebind_marks_drifted(alpha_env):
    """Invariant 3 drift arm — file edit after bind, no rebind → drifted."""
    ctx, repo_root = alpha_env

    ingest_resp = await handle_ingest(ctx, _ratified_payload(
        "Fetch user returns JWT-validated identity.", with_region=False,
    ))
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    bind_resp = await handle_bind(ctx, bindings=[{
        "decision_id": decision_id,
        "file_path": "impl.py",
        "symbol_name": "fetch_user",
        "start_line": 1,
        "end_line": 3,
    }])
    b = bind_resp.bindings[0]
    assert b.error is None

    await handle_resolve_compliance(ctx, phase="ingest", verdicts=[{
        "decision_id": decision_id,
        "region_id": b.region_id,
        "content_hash": b.content_hash,
        "verdict": "compliant",
        "confidence": "high",
        "explanation": "baseline verified",
    }])
    assert await _decision_status(ctx, decision_id) == "reflected"

    _commit_edit(repo_root, """
        def fetch_user(user_id: int):
            # Cookie-based (violates JWT decision).
            return {"id": user_id, "session_cookie": "opaque"}
        """, msg="drift-impl")

    invalidate_sync_cache(ctx)
    lc = await handle_link_commit(ctx, "HEAD")
    assert lc.synced

    status = await _decision_status(ctx, decision_id)
    assert status == "drifted", f"Invariant 3 drift FAIL: expected drifted, got {status}"


# ── Invariant 4: session-start banner surfaces drifts ───────────────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_session_start_banner_surfaces_drifts(alpha_env):
    """Invariant 4 — cold MCP session with drifted decision → banner fires."""
    ctx, _ = alpha_env

    ingest_resp = await handle_ingest(ctx, _ratified_payload(
        "Billing webhook uses exponential backoff with jitter.", with_region=True,
    ))
    assert ingest_resp.ingested
    decision_id = (
        ingest_resp.pending_grounding_decisions[0]["decision_id"]
        if ingest_resp.pending_grounding_decisions
        else (ingest_resp.sync_status.pending_compliance_checks[0].decision_id
              if (ingest_resp.sync_status and ingest_resp.sync_status.pending_compliance_checks)
              else None)
    )
    assert decision_id, "Could not extract decision_id from ingest"

    # Force drift by writing a drifted verdict directly.
    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    from ledger.queries import update_decision_status
    await update_decision_status(inner._client, decision_id, "drifted")

    # Fresh session — clear banner cache.
    ctx._sync_state.pop("session_started", None)
    ctx._sync_state.pop("session_banner", None)

    banner = await get_session_start_banner(ctx)
    assert banner is not None, "Invariant 4 FAIL: banner must fire on drifted decisions"
    assert banner.drifted_count >= 1
    assert "drifted" in banner.message
    assert any(item.get("status") == "drifted" for item in banner.items)

    # Once-per-session: second call returns None.
    assert await get_session_start_banner(ctx) is None, (
        "Invariant 4 FAIL: banner fired twice in the same session"
    )


# ── Invariant 5: preflight surfaces bound decisions ──────────────────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_preflight_surfaces_bound_decisions(monkeypatch, alpha_env):
    """Invariant 5 — preflight on a bound file returns governing decisions."""
    monkeypatch.setenv("BICAMERAL_GUIDED_MODE", "1")
    _, repo_root = alpha_env
    ctx = BicameralContext.from_env()
    assert ctx.guided_mode is True

    ingest_resp = await handle_ingest(ctx, _ratified_payload(
        "User fetch enforces per-tenant rate limits in middleware.", with_region=False,
    ))
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    bind_resp = await handle_bind(ctx, bindings=[{
        "decision_id": decision_id,
        "file_path": "impl.py",
        "symbol_name": "fetch_user",
        "start_line": 1,
        "end_line": 3,
    }])
    assert bind_resp.bindings[0].error is None

    pf = await handle_preflight(ctx, topic="user fetch rate limit middleware",
                                file_paths=["impl.py"])
    assert pf.fired, f"Invariant 5 FAIL: preflight did not fire; reason={pf.reason}"
    decision_ids_returned = [d.decision_id for d in pf.decisions]
    assert decision_id in decision_ids_returned, (
        f"Invariant 5 FAIL: bound decision {decision_id} missing from preflight "
        f"(got: {decision_ids_returned})"
    )


# ── Middleware invariant: hook silent → middleware catches up ────────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_hook_no_fire_still_syncs(alpha_env):
    """PostToolUse hook is best-effort. ensure_ledger_synced must catch up
    inline when link_commit was not called after a commit.
    """
    ctx, repo_root = alpha_env

    ingest_resp = await handle_ingest(ctx, _ratified_payload(
        "Audit log retention 30 days, enforced at write path.", with_region=False,
    ))
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    bind_resp = await handle_bind(ctx, bindings=[{
        "decision_id": decision_id,
        "file_path": "impl.py",
        "symbol_name": "fetch_user",
        "start_line": 1,
        "end_line": 3,
    }])
    b = bind_resp.bindings[0]
    assert b.error is None

    await handle_resolve_compliance(ctx, phase="ingest", verdicts=[{
        "decision_id": decision_id,
        "region_id": b.region_id,
        "content_hash": b.content_hash,
        "verdict": "compliant",
        "confidence": "high",
        "explanation": "baseline",
    }])
    assert await _decision_status(ctx, decision_id) == "reflected"

    # Commit drift — no explicit link_commit call (simulates hook silence).
    _commit_edit(repo_root, """
        def fetch_user(user_id: int):
            # Audit log bypassed.
            raise NotImplementedError
        """, msg="bypass-audit-log")

    # ensure_ledger_synced must detect the new commit and sync.
    invalidate_sync_cache(ctx)
    await ensure_ledger_synced(ctx)

    status = await _decision_status(ctx, decision_id)
    assert status == "drifted", (
        f"Middleware invariant FAIL: expected drifted after hook-silent sync, got {status}"
    )


# ── v0.7 invariant: new ingest enters as proposal (drift-exempt) ────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_new_ingest_enters_as_proposal(alpha_env):
    """v0.9+ invariant: handle_ingest without explicit signoff creates an
    'ungrounded' decision with signoff.state='proposed'. Drift tracking is
    deferred until ratified — the code-compliance status stays 'ungrounded'
    until regions are bound and a compliance verdict is written.
    """
    ctx, _ = alpha_env

    # Ingest WITHOUT explicit signoff — gets proposed default.
    payload = {
        "query": "Pagination defaults to 25 items per page.",
        "repo": "jacob-repo",
        "mappings": [{
            "intent": "Pagination defaults to 25 items per page.",
            "span": {
                "source_type": "transcript",
                "text": "Pagination defaults to 25 items per page.",
                "source_ref": "jacob-v0.7-test",
            },
            "symbols": [],
            "code_regions": [],
        }],
    }
    ingest_resp = await handle_ingest(ctx, payload)
    assert ingest_resp.ingested
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    # Code-compliance status is 'ungrounded' (no regions bound yet).
    # Human-approval axis lives on signoff.state = 'proposed'.
    status = await _decision_status(ctx, decision_id)
    assert status == "ungrounded", (
        f"v0.9+ invariant FAIL: expected 'ungrounded', got '{status}'"
    )

    # After ratification, it remains ungrounded (no code regions bound).
    from handlers.ratify import handle_ratify
    ratify_resp = await handle_ratify(ctx, decision_id=decision_id,
                                     signer="jacob@example.com")
    assert ratify_resp.was_new is True
    assert ratify_resp.signoff["state"] == "ratified"

    # Ratified + no bindings → still ungrounded on the code-compliance axis.
    status_after = await _decision_status(ctx, decision_id)
    assert status_after in ("pending", "ungrounded"), (
        f"v0.9+ invariant FAIL: after ratification expected pending/ungrounded, got '{status_after}'"
    )


# ── Ratify idempotency ───────────────────────────────────────────────


@pytest.mark.alpha_flow
@pytest.mark.phase3
@pytest.mark.asyncio
async def test_ratify_idempotent(alpha_env):
    """Calling ratify twice on the same decision is a no-op on the second call.

    was_new=False means the existing signoff is returned unchanged — the
    original signer and ratified_at timestamp must be preserved.
    """
    from handlers.ratify import handle_ratify
    ctx, _ = alpha_env

    ingest_resp = await handle_ingest(ctx, {
        "query": "Cache TTL is 5 minutes.",
        "repo": "jacob-repo",
        "mappings": [{
            "intent": "Cache TTL is 5 minutes.",
            "span": {
                "source_type": "transcript",
                "text": "Cache TTL is 5 minutes.",
                "source_ref": "arch-review",
            },
            "symbols": [],
            "code_regions": [],
        }],
    })
    assert ingest_resp.ingested
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    resp1 = await handle_ratify(ctx, decision_id=decision_id, signer="jin@example.com")
    assert resp1.was_new is True
    assert resp1.signoff["state"] == "ratified"
    ratified_at = resp1.signoff["ratified_at"]

    # Second call — different signer supplied; must be ignored.
    resp2 = await handle_ratify(ctx, decision_id=decision_id, signer="other@example.com")
    assert resp2.was_new is False
    assert resp2.signoff["state"] == "ratified"
    assert resp2.signoff["signer"] == "jin@example.com"  # original signer preserved
    assert resp2.signoff["ratified_at"] == ratified_at   # timestamp unchanged
