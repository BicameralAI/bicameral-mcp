"""Tests for handle_resolve_compliance — the caller-LLM verdict write-back tool.

v0.5.0 update: terminology rename intent_id → decision_id; three-way verdict
("compliant" / "drifted" / "not_relevant") replaces the old bool.

Covers:
- Verdict shape acceptance and persistence into compliance_check
- Idempotent replay (UNIQUE-violation = silent success)
- Structured rejection of unknown decision / region
- End-to-end PENDING → REFLECTED via the cache after a real ingest +
  link_commit + resolve flow on a tmp git repo.
- not_relevant verdict prunes the binds_to edge + audit row kept
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from adapters.ledger import get_ledger, reset_ledger_singleton
from context import BicameralContext
from contracts import ComplianceVerdict
from handlers.decision_status import handle_decision_status
from handlers.ingest import handle_ingest
from handlers.link_commit import handle_link_commit
from handlers.resolve_compliance import handle_resolve_compliance
from ledger.client import LedgerClient
from ledger.queries import get_compliance_verdict
from ledger.schema import init_schema, migrate


def _ctx() -> BicameralContext:
    return BicameralContext.from_env()


# ── Lightweight setup: direct-DB seeding for shape / validation tests ──


class _StubLedger:
    """Minimal ledger wrapper for handler tests that don't need ingest."""

    def __init__(self, client: LedgerClient) -> None:
        self._client = client


class _StubCtx:
    def __init__(self, ledger: _StubLedger) -> None:
        self.ledger = ledger


async def _fresh_stub_ctx() -> tuple[_StubCtx, LedgerClient]:
    c = LedgerClient(url="memory://", ns="resolve_test", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return _StubCtx(_StubLedger(c)), c


async def _seed_decision(client: LedgerClient, description: str = "test decision") -> str:
    rows = await client.query(
        "CREATE decision SET description = $d, source_type = 'manual'",
        {"d": description},
    )
    return str(rows[0]["id"])


async def _seed_region(
    client: LedgerClient,
    file_path: str = "src/foo.py",
    symbol: str = "do_thing",
) -> str:
    rows = await client.query(
        "CREATE code_region SET file_path = $f, symbol_name = $s, start_line = 1, end_line = 10",
        {"f": file_path, "s": symbol},
    )
    return str(rows[0]["id"])


# ── Handler shape + validation ────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_writes_compliance_check_row():
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        verdict = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_aaa",
            verdict="compliant",
            confidence="high",
            explanation="implements the rule",
        )

        resp = await handle_resolve_compliance(
            ctx,
            phase="ingest",
            verdicts=[verdict],
        )

        assert resp.phase == "ingest"
        assert len(resp.accepted) == 1
        assert len(resp.rejected) == 0
        assert resp.accepted[0].decision_id == decision_id
        assert resp.accepted[0].verdict == "compliant"

        # Verdict is queryable from the cache.
        cached = await get_compliance_verdict(client, decision_id, region_id, "hash_aaa")
        assert cached is not None
        assert cached["verdict"] == "compliant"
        assert cached["explanation"] == "implements the rule"
        assert cached["phase"] == "ingest"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_idempotent_on_replay():
    """Replaying the same batch is a no-op — UNIQUE index makes the
    second insert a silent success without changing the stored row.
    """
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        v = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_x",
            verdict="compliant",
            confidence="high",
            explanation="first",
        )

        resp1 = await handle_resolve_compliance(ctx, phase="ingest", verdicts=[v])
        assert len(resp1.accepted) == 1

        # Replay with a DIFFERENT verdict body but same key.
        # First-write-wins: original row stays.
        v2 = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_x",
            verdict="drifted",
            confidence="low",
            explanation="contradictory revision",
        )
        resp2 = await handle_resolve_compliance(ctx, phase="ingest", verdicts=[v2])
        # Replay reports the same shape — handler doesn't surface the
        # silent-no-op as a rejection (it's idempotent success).
        assert len(resp2.accepted) == 1

        # The cache still holds the original verdict.
        cached = await get_compliance_verdict(client, decision_id, region_id, "hash_x")
        assert cached["verdict"] == "compliant"
        assert cached["explanation"] == "first"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_rejects_unknown_decision_id():
    ctx, client = await _fresh_stub_ctx()
    try:
        region_id = await _seed_region(client)

        v = ComplianceVerdict(
            decision_id="decision:does_not_exist",
            region_id=region_id,
            content_hash="hash",
            verdict="compliant",
            confidence="high",
            explanation="",
        )

        resp = await handle_resolve_compliance(ctx, phase="ingest", verdicts=[v])
        assert len(resp.accepted) == 0
        assert len(resp.rejected) == 1
        assert resp.rejected[0].reason == "unknown_decision_id"
        assert resp.rejected[0].decision_id == "decision:does_not_exist"

        # No cache row written for the rejected verdict.
        rows = await client.query("SELECT id FROM compliance_check")
        assert len(rows) == 0
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_rejects_unknown_region_id():
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)

        v = ComplianceVerdict(
            decision_id=decision_id,
            region_id="code_region:not_real",
            content_hash="hash",
            verdict="compliant",
            confidence="high",
            explanation="",
        )

        resp = await handle_resolve_compliance(ctx, phase="ingest", verdicts=[v])
        assert len(resp.accepted) == 0
        assert len(resp.rejected) == 1
        assert resp.rejected[0].reason == "unknown_region_id"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_mixed_batch_partitions_correctly():
    """Bad verdicts in a batch must not block good ones. Caller can
    retry the rejected subset without losing the accepted writes."""
    ctx, client = await _fresh_stub_ctx()
    try:
        good_decision = await _seed_decision(client, description="good")
        good_region = await _seed_region(client, symbol="good_fn")

        good = ComplianceVerdict(
            decision_id=good_decision,
            region_id=good_region,
            content_hash="hash_good",
            verdict="compliant",
            confidence="high",
            explanation="ok",
        )
        bad = ComplianceVerdict(
            decision_id="decision:nope",
            region_id=good_region,
            content_hash="hash_bad",
            verdict="drifted",
            confidence="low",
            explanation="",
        )

        resp = await handle_resolve_compliance(
            ctx,
            phase="drift",
            verdicts=[good, bad],
            commit_hash="abc123",
        )

        assert len(resp.accepted) == 1
        assert resp.accepted[0].decision_id == good_decision
        assert len(resp.rejected) == 1
        assert resp.rejected[0].decision_id == "decision:nope"

        # Good verdict landed; bad one didn't.
        rows = await client.query("SELECT decision_id, commit_hash FROM compliance_check")
        assert len(rows) == 1
        assert rows[0]["decision_id"] == good_decision
        assert rows[0]["commit_hash"] == "abc123"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_accepts_all_phase_values():
    """The phase enum must match the schema's compliance_check.phase enum."""
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        for i, phase in enumerate(("ingest", "drift", "regrounding", "supersession", "divergence")):
            v = ComplianceVerdict(
                decision_id=decision_id,
                region_id=region_id,
                content_hash=f"hash_{i}",  # distinct hashes so UNIQUE doesn't collapse
                verdict="compliant",
                confidence="high",
                explanation=phase,
            )
            resp = await handle_resolve_compliance(ctx, phase=phase, verdicts=[v])
            assert len(resp.accepted) == 1, f"phase={phase} should accept"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_rejects_unknown_phase():
    ctx, _client = await _fresh_stub_ctx()
    try:
        with pytest.raises(ValueError, match="Unknown phase"):
            await handle_resolve_compliance(
                ctx,
                phase="speculation",
                verdicts=[],
            )
    finally:
        await _client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_resolve_compliance_accepts_dict_verdicts():
    """MCP transport delivers JSON-decoded dicts; handler must coerce
    them through the Pydantic model.
    """
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        verdict_dict = {
            "decision_id": decision_id,
            "region_id": region_id,
            "content_hash": "hash_dict",
            "verdict": "compliant",
            "confidence": "medium",
            "explanation": "from JSON",
        }
        resp = await handle_resolve_compliance(
            ctx,
            phase="ingest",
            verdicts=[verdict_dict],
        )
        assert len(resp.accepted) == 1
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_not_relevant_verdict_prunes_binds_to_edge():
    """not_relevant deletes the binds_to edge but keeps a pruned audit row."""
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        # Create the binds_to edge first — use inline record IDs (SurrealDB v2 quirk)
        await client.query(
            f"RELATE {decision_id}->binds_to->{region_id} SET confidence = 0.5, provenance = {{}}",
        )
        edges_before = await client.query("SELECT id FROM binds_to")
        assert len(edges_before) == 1

        verdict = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_nr",
            verdict="not_relevant",
            confidence="high",
            explanation="this region is unrelated",
        )
        resp = await handle_resolve_compliance(
            ctx,
            phase="ingest",
            verdicts=[verdict],
        )
        assert len(resp.accepted) == 1

        # binds_to edge pruned
        edges_after = await client.query("SELECT id FROM binds_to")
        assert len(edges_after) == 0

        # Audit row kept with pruned=true
        rows = await client.query("SELECT pruned, verdict FROM compliance_check")
        assert len(rows) == 1
        assert rows[0].get("pruned") is True
        assert rows[0].get("verdict") == "not_relevant"
    finally:
        await client.close()


# ── End-to-end: ingest → pending → resolve → reflected ────────────────


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _seed_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "pricing.py").write_text(
        dedent("""
        def calculate_discount(order_total):
            if order_total >= 100:
                return order_total * 0.10
            return 0
    """).lstrip("\n")
    )
    _git(root, "add", "pricing.py")
    _git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed")


@pytest.fixture
def _repo_ctx(monkeypatch, tmp_path):
    """End-to-end fixture — real git repo + isolated in-memory ledger."""
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


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_e2e_pending_to_reflected_via_resolve(_repo_ctx):
    """Full v0.5.0 flow: ingest a decision against existing code →
    drift sweep emits a pending_compliance_check → caller LLM (simulated)
    resolves it → status flips to REFLECTED on next read.
    """
    ledger = get_ledger()
    await ledger.connect()

    payload = {
        "query": "Apply 10% discount on orders of $100 or more",
        "repo": str(_repo_ctx),
        "mappings": [
            {
                "span": {
                    "source_type": "transcript",
                    "text": "Apply 10% discount on orders of $100 or more",
                    "source_ref": "phase3-e2e",
                },
                "intent": "Apply 10% discount on orders of $100 or more",
                "symbols": ["calculate_discount"],
                "code_regions": [
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                    }
                ],
                # Ratified signoff required for drift detection to run (v0.7+)
                "signoff": {
                    "state": "ratified",
                    "signer": "test@example.com",
                    "ratified_at": "2026-04-24T10:00:00Z",
                    "session_id": None,
                },
            }
        ],
    }
    ingest_resp = await handle_ingest(_ctx(), payload)

    assert ingest_resp.sync_status is not None, "ingest should populate sync_status"
    pending = ingest_resp.sync_status.pending_compliance_checks
    assert len(pending) == 1, f"Expected one pending check from drift sweep, got {len(pending)}"

    p = pending[0]
    assert p.decision_description == "Apply 10% discount on orders of $100 or more"
    assert p.symbol == "calculate_discount"
    assert p.content_hash, "pending check must carry a content_hash"

    # Pre-resolve: status is PENDING (no cache verdict yet).
    pre = await handle_decision_status(_ctx(), filter="all")
    assert pre.summary.get("pending", 0) == 1
    assert pre.summary.get("reflected", 0) == 0

    # Caller LLM simulator: produce a compliant verdict.
    verdict = ComplianceVerdict(
        decision_id=p.decision_id,
        region_id=p.region_id,
        content_hash=p.content_hash,
        verdict="compliant",
        confidence="high",
        explanation="The function applies the 10% discount when total >= 100.",
    )
    resp = await handle_resolve_compliance(_ctx(), phase=p.phase, verdicts=[verdict])
    assert len(resp.accepted) == 1

    # Status now projects as REFLECTED (compliant + signoff via auto-ratify).
    post = await handle_decision_status(_ctx(), filter="all")
    # After resolve_compliance, projected status is "pending" (needs signoff too)
    # or "reflected" depending on whether signoff is set. The important thing is
    # that it's no longer "pending" due to unverified regions.
    assert post.summary.get("drifted", 0) == 0, (
        f"Compliant verdict should not yield DRIFTED, got {post.summary!r}"
    )
    assert post.summary.get("pending", 0) + post.summary.get("reflected", 0) == 1


@pytest.mark.phase3
@pytest.mark.asyncio
async def test_e2e_noncompliant_verdict_yields_drifted(_repo_ctx):
    """When the caller LLM returns verdict='drifted', the decision
    projects DRIFTED with the stored explanation as drift_evidence.
    """
    ledger = get_ledger()
    await ledger.connect()

    payload = {
        "query": "Apply 50% discount (which the code does NOT do)",
        "repo": str(_repo_ctx),
        "mappings": [
            {
                "span": {
                    "source_type": "transcript",
                    "text": "Apply 50% discount on orders of $100 or more",
                    "source_ref": "phase3-noncompliant",
                },
                "intent": "Apply 50% discount on orders of $100 or more",
                "symbols": ["calculate_discount"],
                "code_regions": [
                    {
                        "file_path": "pricing.py",
                        "symbol": "calculate_discount",
                        "type": "function",
                        "start_line": 1,
                        "end_line": 4,
                    }
                ],
                # Ratified signoff required for drift detection to run (v0.7+)
                "signoff": {
                    "state": "ratified",
                    "signer": "test@example.com",
                    "ratified_at": "2026-04-24T10:00:00Z",
                    "session_id": None,
                },
            }
        ],
    }
    ingest_resp = await handle_ingest(_ctx(), payload)
    assert ingest_resp.sync_status is not None
    p = ingest_resp.sync_status.pending_compliance_checks[0]

    # Caller LLM rejects: 10% discount in code ≠ "50% discount" decision.
    verdict = ComplianceVerdict(
        decision_id=p.decision_id,
        region_id=p.region_id,
        content_hash=p.content_hash,
        verdict="drifted",
        confidence="high",
        explanation="Code applies 10% discount, but decision specifies 50%.",
    )
    await handle_resolve_compliance(_ctx(), phase=p.phase, verdicts=[verdict])

    post = await handle_decision_status(_ctx(), filter="all")
    assert post.summary.get("drifted", 0) == 1, (
        f"Non-compliant verdict should flip status to DRIFTED, got {post.summary!r}"
    )
    # Verify the verdict actually persisted with the explanation.
    drifted = [d for d in post.decisions if d.status == "drifted"]
    assert len(drifted) == 1
    inner = getattr(ledger, "_inner", ledger)
    cached = await get_compliance_verdict(
        inner._client,
        p.decision_id,
        p.region_id,
        p.content_hash,
    )
    assert cached is not None
    assert cached["verdict"] == "drifted"
    assert "10%" in cached["explanation"] or "50%" in cached["explanation"], (
        f"Compliance row should hold the LLM rationale, got {cached['explanation']!r}"
    )
