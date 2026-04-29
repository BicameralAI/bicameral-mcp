"""Alpha release contract — five-invariant end-to-end regression.

These are the load-bearing behaviors we promise our first wave of alpha
users. The v0.7.0 refactor (signoff/ratify schema change) ships only if
all five tests pass green on that branch. No exceptions.

Invariants (see ``thoughts/shared/plans/2026-04-24-pre-monday-jacob-guardrails.md``):

1. **Ingest** — decisions from a meeting/doc payload land in the ledger,
   searchable by feature area.
2. **Bind** — caller LLM links decisions to code symbols via
   ``bicameral_bind``; every edge is author-attested (provenance.method=
   caller_llm).
3. **Commit** — ``git push`` → ``link_commit`` runs (or
   ``ensure_ledger_synced`` catches up) → bound regions are hashed →
   decisions are marked ``reflected`` or ``drifted``.
4. **Session-start banner** — opening any MCP session surfaces drifted
   decisions unprompted via ``get_session_start_banner``.
5. **Preflight** — ``bicameral_preflight`` on a bound file returns its
   governing decisions.

Unit-level coverage already exists in ``test_bind.py``,
``test_sync_middleware.py``, ``test_link_commit_grounding.py``,
``test_v0412_preflight.py``, ``test_v055_region_anchored_preflight.py``.
This file is the end-to-end contract — real git repo, real ledger,
real commits — labeled under one suite so the v0.7.0 refactor can be
gated on it.
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
from handlers.preflight import handle_preflight
from handlers.resolve_compliance import handle_resolve_compliance
from handlers.search_decisions import handle_search_decisions
from handlers.sync_middleware import ensure_ledger_synced, get_session_start_banner

# ── Git + ingest helpers ─────────────────────────────────────────────


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
    # Authenticated JWT lookup, not cookie-based.
    return {"id": user_id, "name": "Test User"}
"""


def _seed_repo(repo_root: Path, body: str = _INITIAL_IMPL) -> None:
    """Create a fresh git repo with a single tracked Python file."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "alpha@example.com")
    _git(repo_root, "config", "user.name", "Alpha User")
    (repo_root / "impl.py").write_text(dedent(body).strip() + "\n")
    _git(repo_root, "add", ".")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")


def _commit_edit(repo_root: Path, new_body: str, msg: str = "edit") -> None:
    (repo_root / "impl.py").write_text(dedent(new_body).strip() + "\n")
    _git(repo_root, "add", "impl.py")
    _git(repo_root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", msg)


def _ingest_payload(description: str, *, with_region: bool, signoff: bool) -> dict:
    """Build an internal-format ingest payload.

    ``with_region=True``   → mapping pre-pinned to ``impl.py:fetch_user``.
    ``with_region=False``  → ungrounded decision (invariant 2 then binds it).
    ``signoff=True``       → attaches ``signoff`` so
                             ``project_decision_status`` can promote to
                             ``reflected`` once compliance is resolved.
    """
    mapping: dict = {
        "intent": description,
        "span": {
            "source_type": "transcript",
            "text": description,
            "source_ref": "alpha-architecture-review-2026-04-24",
            "speakers": ["alpha@example.com"],
            "meeting_date": "2026-04-24",
        },
        "symbols": [],
        "code_regions": [],
    }
    if with_region:
        mapping["code_regions"] = [
            {
                "file_path": "impl.py",
                "symbol": "fetch_user",
                "type": "function",
                "start_line": 1,
                "end_line": 3,
                "purpose": description,
            }
        ]
    if signoff:
        mapping["signoff"] = {
            "state": "ratified",
            "signer": "alpha@example.com",
            "ratified_at": "2026-04-24T10:00:00Z",
            "session_id": None,
        }
    return {
        "query": description,
        "repo": "alpha-repo",
        "mappings": [mapping],
    }


async def _decision_status(ctx, decision_id: str) -> str:
    """Read ``decision.status`` directly from SurrealDB."""
    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    await inner._ensure_connected()
    rows = await inner._client.query(f"SELECT status FROM {decision_id} LIMIT 1")
    return str((rows or [{}])[0].get("status", "")) if rows else ""


async def _first_decision_id(ctx) -> str:
    """Return the first decision node id as a string."""
    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    await inner._ensure_connected()
    rows = await inner._client.query("SELECT id FROM decision LIMIT 1")
    assert rows, "expected at least one decision in the ledger"
    return str(rows[0]["id"])


async def _force_drift(ctx, decision_id: str) -> None:
    """Direct write — promote a decision to ``drifted`` without driving the
    full drift-sweep pipeline. Used in the banner-only test where the other
    invariants are already covered by their own tests.
    """
    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    await inner._ensure_connected()
    await inner._client.query(f"UPDATE {decision_id} SET status = 'drifted'")


# ── Fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def alpha_env(monkeypatch, tmp_path):
    """Fresh in-memory ledger + fresh git repo per test.

    Yields ``(ctx, repo_root)`` so each test can edit files, commit,
    and re-enter handlers.
    """
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo_root = tmp_path / "alpha-repo"
    _seed_repo(repo_root)

    monkeypatch.setenv("REPO_PATH", str(repo_root))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo_root)

    reset_ledger_singleton()
    ctx = BicameralContext.from_env()
    yield ctx, repo_root
    reset_ledger_singleton()


# ── Invariant 1 + 2 + 3: ingest → bind → commit → reflected ────────


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_ingest_bind_commit_marks_reflected(alpha_env):
    """Happy path: ingested decision, caller-LLM bind, compliant verdict →
    decision projects to ``reflected``.

    Proves invariants 1, 2, 3 together. ``project_decision_status`` only
    returns ``reflected`` when every binding has a ``compliant`` verdict
    AND ``signoff`` is set — which is what a PM-ratified, engineer-
    verified decision looks like in the alpha flow.
    """
    ctx, _ = alpha_env

    # 1. Ingest (ungrounded, with signoff — PM was in the room).
    ingest_resp = await handle_ingest(
        ctx,
        _ingest_payload(
            "JWT is the session-authentication primitive, not cookies.",
            with_region=False,
            signoff=True,
        ),
    )
    assert ingest_resp.ingested
    assert ingest_resp.stats.intents_created == 1
    assert len(ingest_resp.pending_grounding_decisions) == 1
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    # Decision is searchable by description tokens (invariant 1 — "searchable
    # by feature area"). Uses BM25 via handle_search_decisions.
    search_resp = await handle_search_decisions(
        ctx,
        query="JWT session authentication",
        max_results=5,
    )
    assert any(m.decision_id == decision_id for m in search_resp.matches), (
        "ingested decision must be retrievable via BM25 search"
    )

    # 2. Caller-LLM bind (invariant 2, author-attested via provenance=caller_llm).
    bind_resp = await handle_bind(
        ctx,
        bindings=[
            {
                "decision_id": decision_id,
                "file_path": "impl.py",
                "symbol_name": "fetch_user",
                "start_line": 1,
                "end_line": 3,
                "purpose": "JWT validation entrypoint",
            }
        ],
    )
    assert len(bind_resp.bindings) == 1
    b = bind_resp.bindings[0]
    assert b.error is None, f"bind failed: {b.error}"
    assert b.region_id
    assert b.content_hash, "content_hash must be populated for git-tracked files"

    # 3. Resolve compliance (caller-LLM verdict — code implements decision).
    rc_resp = await handle_resolve_compliance(
        ctx,
        phase="ingest",
        verdicts=[
            {
                "decision_id": decision_id,
                "region_id": b.region_id,
                "content_hash": b.content_hash,
                "verdict": "compliant",
                "confidence": "high",
                "explanation": "fetch_user performs JWT lookup as decided.",
            }
        ],
    )
    assert len(rc_resp.accepted) == 1
    assert not rc_resp.rejected

    # Holistic projection: all bindings compliant + signoff set → reflected.
    assert await _decision_status(ctx, decision_id) == "reflected"


# ── Invariant 3 (drift arm): file edit without rebind → drifted ───


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_code_edit_without_rebind_marks_drifted(alpha_env):
    """After reflected, edit the bound symbol and commit. link_commit
    re-sweeps, hash no longer matches the verdict, prior compliant verdict
    exists → ``project_decision_status`` returns ``drifted``.
    """
    ctx, repo_root = alpha_env

    # Reach reflected state first.
    ingest_resp = await handle_ingest(
        ctx,
        _ingest_payload(
            "Fetch user flow returns JWT-validated identity.",
            with_region=False,
            signoff=True,
        ),
    )
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    bind_resp = await handle_bind(
        ctx,
        bindings=[
            {
                "decision_id": decision_id,
                "file_path": "impl.py",
                "symbol_name": "fetch_user",
                "start_line": 1,
                "end_line": 3,
            }
        ],
    )
    b = bind_resp.bindings[0]
    assert b.error is None

    await handle_resolve_compliance(
        ctx,
        phase="ingest",
        verdicts=[
            {
                "decision_id": decision_id,
                "region_id": b.region_id,
                "content_hash": b.content_hash,
                "verdict": "compliant",
                "confidence": "high",
                "explanation": "baseline verified",
            }
        ],
    )
    assert await _decision_status(ctx, decision_id) == "reflected"

    # Edit the bound symbol body — hash must change.
    _commit_edit(
        repo_root,
        """
        def fetch_user(user_id: int):
            # Cookie-based session (violates the decision).
            return {"id": user_id, "session_cookie": "opaque"}
        """,
        msg="drift impl",
    )

    # link_commit must re-sweep — cache would otherwise short-circuit on the
    # new HEAD within the same ctx.
    invalidate_sync_cache(ctx)
    lc_resp = await handle_link_commit(ctx, "HEAD")
    assert lc_resp.synced

    # Prior compliant verdict + new hash with no verdict → drifted.
    assert await _decision_status(ctx, decision_id) == "drifted"


# ── Invariant 4: session-start banner surfaces drifted decisions ───


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_session_start_banner_surfaces_drifts(alpha_env):
    """Cold MCP session with a drifted decision in the ledger → the first
    call to ``get_session_start_banner`` returns a populated banner.

    End-to-end: an ingested decision whose status has reached ``drifted``
    must surface on session start, regardless of which tool the caller
    invokes first.
    """
    ctx, _ = alpha_env

    ingest_resp = await handle_ingest(
        ctx,
        _ingest_payload(
            "Billing webhook retries use exponential backoff with jitter.",
            with_region=True,
            signoff=True,
        ),
    )
    assert ingest_resp.ingested

    # The decision starts as pending (regions exist but no verdict yet);
    # force drift to test the banner contract in isolation of the
    # commit-flow. Other tests exercise the full drift transition.
    decision_id = await _first_decision_id(ctx)
    await _force_drift(ctx, decision_id)

    # Simulate a fresh MCP session — banner hasn't fired yet this session.
    ctx._sync_state.pop("session_started", None)
    ctx._sync_state.pop("session_banner", None)

    banner = await get_session_start_banner(ctx)
    assert banner is not None, "banner must surface drifted decisions on first call"
    assert banner.drifted_count >= 1
    assert "drifted" in banner.message
    assert any(item.get("status") == "drifted" for item in banner.items)

    # Once-per-session contract: second call must return None.
    assert await get_session_start_banner(ctx) is None


# ── Invariant 5: preflight on a bound file surfaces its decisions ──


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_preflight_surfaces_bound_decisions(monkeypatch, alpha_env):
    """Preflight with a bound file_path returns the governing decision via
    the region-anchored lookup path.

    Uses guided mode so the gate fires on any matches (not just drift/
    ungrounded) — this test proves the *plumbing* surfaces bound decisions,
    not the normal-mode gating logic (covered by ``test_v0412_preflight``).
    """
    # Guided mode is read at ctx construction — set before re-creating ctx.
    monkeypatch.setenv("BICAMERAL_GUIDED_MODE", "1")
    _, repo_root = alpha_env
    ctx = BicameralContext.from_env()
    assert ctx.guided_mode is True

    ingest_resp = await handle_ingest(
        ctx,
        _ingest_payload(
            "User fetch enforces per-tenant rate limits in middleware.",
            with_region=False,
            signoff=True,
        ),
    )
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    bind_resp = await handle_bind(
        ctx,
        bindings=[
            {
                "decision_id": decision_id,
                "file_path": "impl.py",
                "symbol_name": "fetch_user",
                "start_line": 1,
                "end_line": 3,
            }
        ],
    )
    assert bind_resp.bindings[0].error is None

    pf_resp = await handle_preflight(
        ctx,
        topic="editing the user fetch rate limit middleware",
        file_paths=["impl.py"],
    )
    assert pf_resp.fired, f"preflight did not fire; reason={pf_resp.reason}"
    assert "region" in pf_resp.sources_chained
    decision_ids = [d.decision_id for d in pf_resp.decisions]
    assert decision_id in decision_ids, (
        f"bound decision {decision_id} missing from preflight response (got: {decision_ids})"
    )


# ── Middleware contract: hook didn't fire → middleware still syncs ──


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_hook_no_fire_still_syncs(alpha_env):
    """PostToolUse hook is best-effort. The middleware contract is: if
    ``link_commit`` wasn't called after a commit, the next ledger-read
    handler entering via ``ensure_ledger_synced`` must catch up inline.

    Protocol: reach reflected → commit a drift without calling link_commit →
    ``ensure_ledger_synced`` on the ctx should run ``link_commit`` itself and
    the decision status should flip to ``drifted``.
    """
    ctx, repo_root = alpha_env

    ingest_resp = await handle_ingest(
        ctx,
        _ingest_payload(
            "Audit log retention is 30 days, enforced at write path.",
            with_region=False,
            signoff=True,
        ),
    )
    decision_id = ingest_resp.pending_grounding_decisions[0]["decision_id"]

    bind_resp = await handle_bind(
        ctx,
        bindings=[
            {
                "decision_id": decision_id,
                "file_path": "impl.py",
                "symbol_name": "fetch_user",
                "start_line": 1,
                "end_line": 3,
            }
        ],
    )
    b = bind_resp.bindings[0]
    assert b.error is None

    await handle_resolve_compliance(
        ctx,
        phase="ingest",
        verdicts=[
            {
                "decision_id": decision_id,
                "region_id": b.region_id,
                "content_hash": b.content_hash,
                "verdict": "compliant",
                "confidence": "high",
                "explanation": "baseline",
            }
        ],
    )
    assert await _decision_status(ctx, decision_id) == "reflected"

    # Commit drift — and DO NOT call handle_link_commit directly.
    _commit_edit(
        repo_root,
        """
        def fetch_user(user_id: int):
            # Completely different impl — audit log retention bypassed.
            raise NotImplementedError
        """,
        msg="silent drift commit (hook dropped)",
    )

    # ensure_ledger_synced is what handlers like preflight and history
    # call transparently. It should notice live_head > last_sync_sha and
    # inline-run link_commit.
    await ensure_ledger_synced(ctx)

    assert await _decision_status(ctx, decision_id) == "drifted", (
        "ensure_ledger_synced middleware failed to catch up after a "
        "silently-missed PostToolUse hook"
    )
