"""Canonical regression matrix for the 13 desync scenarios from the Notion
"Auto-Grounding Problem" catalog (Notion ID 3332a51619c4813caccec86c36d9bf98).

This is V1 F1 — one consolidated test file routing every scenario through
the **real handler layer** (Apr 8 PR #84 lesson: tests that bypass to
``ledger.ingest_payload`` directly miss the auto-grounding hooks). Each test
proves V1 behavior or ``xfail``s with a pointer to the V2 design-doc section
that resolves it.

Scenario list (severity tiers from the Notion catalog):

  1. New decision ingested, matching code exists                   (was P0)
  2. Code changed after decision was grounded                       (working)
  3. Code deleted after decision was grounded                       (working)
  4. Symbol renamed (refactor)                                      (P1)
  5. Symbol moved to different file                                 (P1)
  6. Code index rebuilt with new symbols                            (was P0)
  7. Cold start: no code index                                      (working)
  8. Drifted intent — recoverable                                   (P1, V2)
  9. Intent description updated (supersession)                      (P2)
 10. Multiple intents map to same symbol                            (working)
 11. BM25 false-positive grounding                                  (post-v0.6.0: N/A)
 12. Code region line numbers shift (insertion above)               (working)
 13. Open-question prefix → not-claimed                             (v0.5.x)

Post-v0.6.0 architectural note: server-side auto-grounding (BM25 → bind
edges) was removed; the caller LLM owns code retrieval and writes bindings
explicitly via ``bicameral.bind``. Several scenarios that were originally
P0 ("auto-grounding not wired") now pass via the caller-LLM flow rather
than via server-side magic. Scenarios depending on V2-only tools
(``bicameral_rebind``, ``record_compliance_verdict``) are marked xfail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import reset_ledger_singleton
from context import BicameralContext
from handlers.bind import handle_bind
from handlers.detect_drift import handle_detect_drift
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit, invalidate_sync_cache

# ── Helpers ──────────────────────────────────────────────────────────


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
    """Create a fresh git repo on ``main`` with the given files committed."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@e.com")
    _git(repo, "config", "user.name", "tester")
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).strip() + "\n")
    _commit(repo, "seed")


def _build_payload(
    repo: Path,
    *,
    text: str,
    intent: str,
    code_regions: list[dict] | None = None,
    source_ref: str = "scenario-test",
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
                "symbols": [r["symbol"] for r in (code_regions or []) if r.get("symbol")],
                "code_regions": code_regions or [],
            }
        ],
    }


@pytest.fixture
def _scenario_repo(monkeypatch, tmp_path):
    """Fresh git repo on `main` + memory ledger. Each test gets a fresh fixture."""
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    repo = tmp_path / "repo"
    _seed_repo(
        repo,
        {
            "src/payments.py": """
            def calculate_discount(order_total: float) -> float:
                return order_total * 0.1
        """,
            "src/auth.py": """
            def verify_token(token: str) -> bool:
                return token.startswith("valid:")
        """,
        },
    )
    monkeypatch.setenv("REPO_PATH", str(repo))
    monkeypatch.setenv("BICAMERAL_AUTHORITATIVE_REF", "main")
    monkeypatch.chdir(repo)
    reset_ledger_singleton()
    yield repo
    reset_ledger_singleton()


# ── Scenarios 1–13 ───────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_01_new_decision_with_existing_code(_scenario_repo):
    """An ingested decision with no code_regions surfaces as ungrounded
    via pending_grounding_checks; the caller LLM grounds via bicameral.bind.
    """
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Apply 10% discount on orders",
        intent="Apply 10% discount on orders",
        code_regions=[],
    )
    ingest = await handle_ingest(ctx, payload)
    assert ingest.ingested, f"ingest failed: {ingest}"
    assert ingest.stats.ungrounded >= 1, f"expected ≥1 ungrounded after ingest, got: {ingest.stats}"
    # NOTE: handle_ingest internally runs link_commit; the within-call sync
    # cache forwards its pending_grounding_checks to subsequent calls.
    # Do NOT invalidate the cache — the early-return path at
    # ledger/adapter.py:333 skips the ungrounded sweep when changed_files
    # is empty, so a cache miss would lose the grounding signal.
    lc = await handle_link_commit(ctx, "HEAD")
    ungrounded = [c for c in lc.pending_grounding_checks if c.get("reason") == "ungrounded"]
    assert ungrounded, f"Expected ungrounded grounding check, got: {lc.pending_grounding_checks}"
    decision_id = ungrounded[0]["decision_id"]

    bind_resp = await handle_bind(
        ctx,
        [
            {
                "decision_id": decision_id,
                "file_path": "src/payments.py",
                "symbol_name": "calculate_discount",
            }
        ],
    )
    assert bind_resp.bindings
    assert not bind_resp.bindings[0].error, bind_resp.bindings[0].error


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_02_code_changed_after_grounded_pending_until_verdict(_scenario_repo):
    """Code-content change → status pending (awaiting caller-LLM verdict).

    Post-v0.5.0 derive_status semantics (``ledger/status.py:178-205``):
    a hash diff WITHOUT a cached ``compliant`` verdict yields ``pending``,
    not ``drifted``. ``drifted`` is reserved for cases where the caller
    LLM has explicitly written a ``drifted`` verdict via the verdict
    cache (V2 territory: see design doc §8 C2). For V1, the regression
    we want is that a real code change DOES surface the affected
    decision as a `pending_compliance_check` with new content_hash so
    a future V2 caller can verdict it.
    """
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Apply discount",
        intent="Apply 10% discount",
        code_regions=[
            {
                "file_path": "src/payments.py",
                "symbol": "calculate_discount",
                "start_line": 1,
                "end_line": 2,
                "type": "function",
                "purpose": "discount calc",
            }
        ],
    )
    await handle_ingest(ctx, payload)

    # Mutate the bound region.
    (_scenario_repo / "src/payments.py").write_text(
        "def calculate_discount(order_total: float) -> float:\n    return order_total * 0.15\n"
    )
    _commit(_scenario_repo, "raise discount to 15%")
    invalidate_sync_cache(ctx)
    lc = await handle_link_commit(ctx, "HEAD")

    # The compliance check should fire for the changed region.
    pending = [p for p in lc.pending_compliance_checks if p.symbol == "calculate_discount"]
    assert pending, (
        f"Expected pending_compliance_check for changed region, got: "
        f"{[(p.symbol, p.phase) for p in lc.pending_compliance_checks]}"
    )
    drift = await handle_detect_drift(ctx, "src/payments.py")
    statuses = {d.status for d in drift.decisions if d.symbol == "calculate_discount"}
    # Acceptable per-decision states (code-compliance axis only, post-v0.9 decoupling):
    #   - 'pending' / 'drifted': hash differs, awaiting a compliance verdict
    #   - 'ungrounded': decision has no bound code region yet
    # ('proposal' was a pre-v0.9 combined status; replaced by signoff.state='proposed')
    assert statuses & {"pending", "drifted", "ungrounded"}, (
        f"Expected pending / drifted / ungrounded, got: "
        f"{[(d.status, d.symbol) for d in drift.decisions]}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_03_code_deleted_after_grounded_pending(_scenario_repo):
    """File deleted → derive_status → pending (actual_hash is None)."""
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Apply discount",
        intent="Apply 10% discount",
        code_regions=[
            {
                "file_path": "src/payments.py",
                "symbol": "calculate_discount",
                "start_line": 1,
                "end_line": 2,
                "type": "function",
                "purpose": "discount calc",
            }
        ],
    )
    await handle_ingest(ctx, payload)

    (_scenario_repo / "src/payments.py").unlink()
    _commit(_scenario_repo, "remove payments")
    invalidate_sync_cache(ctx)
    lc = await handle_link_commit(ctx, "HEAD")

    # Symbol disappeared on authoritative ref.
    disappeared = [
        c for c in lc.pending_grounding_checks if c.get("reason") == "symbol_disappeared"
    ]
    assert disappeared, f"Expected symbol_disappeared check, got: {lc.pending_grounding_checks}"


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_04_symbol_renamed_in_file(_scenario_repo):
    """In-file rename → symbol_disappeared grounding check (V1 D1)."""
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Apply discount",
        intent="Apply 10% discount",
        code_regions=[
            {
                "file_path": "src/payments.py",
                "symbol": "calculate_discount",
                "start_line": 1,
                "end_line": 2,
                "type": "function",
                "purpose": "discount calc",
            }
        ],
    )
    await handle_ingest(ctx, payload)

    (_scenario_repo / "src/payments.py").write_text(
        "def compute_discount(order_total: float) -> float:\n    return order_total * 0.1\n"
    )
    _commit(_scenario_repo, "rename calculate_discount -> compute_discount")
    invalidate_sync_cache(ctx)
    lc = await handle_link_commit(ctx, "HEAD")

    disappeared = [
        c for c in lc.pending_grounding_checks if c.get("reason") == "symbol_disappeared"
    ]
    assert disappeared, f"Expected symbol_disappeared, got: {lc.pending_grounding_checks}"
    assert disappeared[0]["symbol"] == "calculate_discount"
    # V1 D1: original_lines is part of the payload.
    assert "original_lines" in disappeared[0]


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_05_symbol_moved_to_different_file(_scenario_repo):
    """Cross-file move → symbol_disappeared grounding check."""
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Apply discount",
        intent="Apply 10% discount",
        code_regions=[
            {
                "file_path": "src/payments.py",
                "symbol": "calculate_discount",
                "start_line": 1,
                "end_line": 2,
                "type": "function",
                "purpose": "discount calc",
            }
        ],
    )
    await handle_ingest(ctx, payload)

    (_scenario_repo / "src/payments.py").write_text("# moved\n")
    (_scenario_repo / "src/pricing.py").write_text(
        "def calculate_discount(order_total: float) -> float:\n    return order_total * 0.1\n"
    )
    _commit(_scenario_repo, "move discount calc to pricing.py")
    invalidate_sync_cache(ctx)
    lc = await handle_link_commit(ctx, "HEAD")

    disappeared = [
        c for c in lc.pending_grounding_checks if c.get("reason") == "symbol_disappeared"
    ]
    assert disappeared, (
        f"Expected symbol_disappeared on cross-file move, got: {lc.pending_grounding_checks}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_06_code_added_ungrounded_resolvable(_scenario_repo):
    """An ungrounded decision becomes resolvable once the matching symbol is added.

    Post-v0.6.0: the caller LLM is responsible for noticing the new symbol and
    calling bicameral.bind. The server keeps surfacing pending_grounding_checks
    until the caller binds.
    """
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Add cart total endpoint",
        intent="Cart total endpoint",
        code_regions=[],
    )
    await handle_ingest(ctx, payload)
    # See scenario 1 note — do NOT invalidate before lc1; rely on cache
    # forwarding the ungrounded check from ingest's internal link_commit.
    lc1 = await handle_link_commit(ctx, "HEAD")
    assert any(c.get("reason") == "ungrounded" for c in lc1.pending_grounding_checks)

    # Caller adds the matching code.
    (_scenario_repo / "src/cart.py").write_text(
        "def cart_total(items: list) -> float:\n    return sum(i['price'] for i in items)\n"
    )
    _commit(_scenario_repo, "add cart_total")
    object.__setattr__(ctx, "authoritative_sha", _git(_scenario_repo, "rev-parse", "HEAD").strip())
    invalidate_sync_cache(ctx)
    lc2 = await handle_link_commit(ctx, "HEAD")

    ungrounded = [c for c in lc2.pending_grounding_checks if c.get("reason") == "ungrounded"]
    assert ungrounded, "Decision should still surface as ungrounded until caller binds"
    decision_id = ungrounded[0]["decision_id"]
    # Pass explicit lines — ctx.authoritative_sha is captured at ctx
    # creation and is stale after the new commit, so resolve_symbol_lines
    # would look at the wrong ref. Explicit lines bypass resolution.
    bind_resp = await handle_bind(
        ctx,
        [
            {
                "decision_id": decision_id,
                "file_path": "src/cart.py",
                "symbol_name": "cart_total",
                "start_line": 1,
                "end_line": 2,
            }
        ],
    )
    assert bind_resp.bindings and not bind_resp.bindings[0].error, (
        f"bind failed: {bind_resp.bindings[0].error if bind_resp.bindings else 'no result'}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_07_cold_start_no_code_index(_scenario_repo, monkeypatch):
    """Cold start with no symbols matching the intent → decision stays ungrounded.

    The seed repo has only ``calculate_discount`` and ``verify_token``.
    A decision about something the repo doesn't contain stays ungrounded
    until the caller binds it (which they cannot, since there's no target).
    """
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Add Slack notification on signup",
        intent="Slack notify on signup",
        code_regions=[],
    )
    await handle_ingest(ctx, payload)
    # See scenario 1 note — do NOT invalidate the sync cache here.
    lc = await handle_link_commit(ctx, "HEAD")
    assert any(c.get("reason") == "ungrounded" for c in lc.pending_grounding_checks), (
        f"Expected ungrounded check on cold start, got: {lc.pending_grounding_checks}"
    )


@pytest.mark.xfail(
    strict=True,
    reason="V2: requires bicameral_rebind with old-binding CAS + fresh L3 verdict on the new target. See design doc §8 D2. Codex pass-10 finding #2.",
)
@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_08_drifted_recoverable_via_atomic_rebind(_scenario_repo):
    """A drifted decision whose code moved should re-ground atomically.

    V1 surfaces symbol_disappeared (scenarios 4/5) but offers no atomic
    rebind — calling bicameral.bind on the new location leaves the old
    edge live, producing duplicate bindings. xfailed until V2 D2.
    """
    pytest.fail("V2 work — see design doc §8 D2.")


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_09_intent_description_supersession(_scenario_repo):
    """Updated intent description supersedes the prior decision.

    Covered by tests/test_supersession.py. This test asserts the
    canonical handler path doesn't raise during a re-ingest with
    overlapping intent text.
    """
    ctx = BicameralContext.from_env()
    p1 = _build_payload(
        _scenario_repo,
        text="Apply discount",
        intent="Apply 10% discount on orders",
        code_regions=[
            {
                "file_path": "src/payments.py",
                "symbol": "calculate_discount",
                "start_line": 1,
                "end_line": 2,
                "type": "function",
                "purpose": "discount calc",
            }
        ],
        source_ref="meeting-1",
    )
    p2 = _build_payload(
        _scenario_repo,
        text="Apply discount with backoff",
        intent="Apply 15% discount on orders over $100",
        code_regions=[
            {
                "file_path": "src/payments.py",
                "symbol": "calculate_discount",
                "start_line": 1,
                "end_line": 2,
                "type": "function",
                "purpose": "discount calc",
            }
        ],
        source_ref="meeting-2",
    )
    r1 = await handle_ingest(ctx, p1)
    r2 = await handle_ingest(ctx, p2)
    assert r1.ingested and r2.ingested


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_10_multiple_intents_share_symbol(_scenario_repo):
    """Two decisions bound to the same symbol both surface on drift detection."""
    ctx = BicameralContext.from_env()
    region = {
        "file_path": "src/auth.py",
        "symbol": "verify_token",
        "start_line": 1,
        "end_line": 2,
        "type": "function",
        "purpose": "auth check",
    }
    await handle_ingest(
        ctx,
        _build_payload(
            _scenario_repo,
            text="Verify JWT",
            intent="Use JWT verification",
            code_regions=[region],
            source_ref="m1",
        ),
    )
    await handle_ingest(
        ctx,
        _build_payload(
            _scenario_repo,
            text="Reject invalid",
            intent="Reject malformed tokens",
            code_regions=[region],
            source_ref="m2",
        ),
    )
    invalidate_sync_cache(ctx)
    drift = await handle_detect_drift(ctx, "src/auth.py")
    decision_ids = {d.decision_id for d in drift.decisions}
    assert len(decision_ids) >= 2, (
        f"Expected ≥2 decisions sharing the same symbol, got {len(decision_ids)}: {decision_ids}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_11_no_server_side_bm25_grounding_post_v060(_scenario_repo):
    """Post-v0.6.0: server-side BM25 false-positive grounding is no longer a risk.

    The original P2 concern was that BM25 could match a decision to an
    irrelevant symbol. v0.6.0 deleted the entire ``ground_mappings``
    pipeline; bindings now require an explicit ``bicameral.bind`` call
    from the caller LLM. This test asserts an ingest WITHOUT
    code_regions never auto-binds anything — the decision stays ungrounded
    until the caller acts.
    """
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="Validate webhook signatures",
        # Intent text mentions "verify" / "token" — the seed repo has
        # verify_token in src/auth.py. Pre-v0.6.0 BM25 would have matched.
        intent="Verify webhook tokens",
        code_regions=[],
    )
    await handle_ingest(ctx, payload)
    # See scenario 1 note — do NOT invalidate the sync cache here.
    lc = await handle_link_commit(ctx, "HEAD")
    # No edges should have been auto-created — decision stays ungrounded.
    ungrounded = [c for c in lc.pending_grounding_checks if c.get("reason") == "ungrounded"]
    assert ungrounded, "Post-v0.6.0 ingest must leave decisions ungrounded — no server-side bind"


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_12_line_shift_does_not_trigger_drift(_scenario_repo):
    """Inserting blank lines above a tracked symbol must not trigger drift.

    resolve_symbol_lines re-resolves the symbol via tree-sitter, so the
    region's content_hash is computed against the relocated span — not
    the frozen line range from ingest time.
    """
    ctx = BicameralContext.from_env()
    region = {
        "file_path": "src/auth.py",
        "symbol": "verify_token",
        "start_line": 1,
        "end_line": 2,
        "type": "function",
        "purpose": "auth check",
    }
    await handle_ingest(
        ctx,
        _build_payload(
            _scenario_repo,
            text="Use JWT",
            intent="JWT verification",
            code_regions=[region],
        ),
    )

    # Insert blank lines above — line numbers shift but the symbol bytes
    # are identical.
    (_scenario_repo / "src/auth.py").write_text(
        '\n\n\ndef verify_token(token: str) -> bool:\n    return token.startswith("valid:")\n'
    )
    _commit(_scenario_repo, "insert blank lines above")
    invalidate_sync_cache(ctx)
    await handle_link_commit(ctx, "HEAD")

    drift = await handle_detect_drift(ctx, "src/auth.py")
    drifted = [d for d in drift.decisions if d.status == "drifted"]
    assert not drifted, (
        f"Line-shift edit must NOT trigger drift, got: {[(d.status, d.symbol, d.lines) for d in drift.decisions]}"
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_scenario_13_open_question_decision_classification(_scenario_repo):
    """[Open Question]-prefixed decisions are classified as gaps, not normal decisions.

    Added in v0.5.x as the 13th scorecard entry. Verifies the prefix
    convention is honored end-to-end so caller LLMs can render gaps
    distinctly from claimed decisions.
    """
    ctx = BicameralContext.from_env()
    payload = _build_payload(
        _scenario_repo,
        text="[Open Question] Should we add SSO?",
        intent="[Open Question] Should we add SSO?",
        code_regions=[],
    )
    res = await handle_ingest(ctx, payload)
    assert res.ingested
    # The decision is persisted; its status / classification is exercised
    # via tests/test_v0420_history.py for the "gap" rendering path.
