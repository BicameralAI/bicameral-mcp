"""#405 — reflected-before-drifted invariant + 'partial' verdict semantics.

The compliance_check verdict enum gained 'partial' (never-compliant
anticipatory binding). The handler refuses to write 'drifted' unless a prior
'compliant' verdict exists for the same (decision_id, region_id), returning
a structured `state_transition_invalid` rejection the caller-LLM can act on
without re-binding.

These tests cover the new write-path semantics in isolation; the full
ingest→link_commit→resolve_compliance round-trip lives in
`test_resolve_compliance.py::test_e2e_noncompliant_verdict_on_never_compliant_yields_partial`.
"""

from __future__ import annotations

import pytest

from contracts import ComplianceVerdict
from handlers.resolve_compliance import handle_resolve_compliance
from ledger.client import LedgerClient
from ledger.queries import compliance_history_summary, get_compliance_verdict
from ledger.schema import init_schema, migrate


class _StubLedger:
    def __init__(self, client: LedgerClient) -> None:
        self._client = client


class _StubCtx:
    def __init__(self, ledger: _StubLedger) -> None:
        self.ledger = ledger


async def _fresh_stub_ctx() -> tuple[_StubCtx, LedgerClient]:
    c = LedgerClient(url="memory://", ns="resolve_state_transition", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return _StubCtx(_StubLedger(c)), c


async def _seed_decision(
    client: LedgerClient,
    description: str = "anticipatory decision",
    canonical: str = "",
) -> str:
    # canonical_id has a UNIQUE index — pass distinct values per row when
    # seeding more than one decision in the same test.
    canon = canonical or f"canon_{description.replace(' ', '_')}"
    rows = await client.query(
        "CREATE decision SET description = $d, source_type = 'manual', canonical_id = $c",
        {"d": description, "c": canon},
    )
    return str(rows[0]["id"])


async def _seed_region(client: LedgerClient, symbol: str = "fn") -> str:
    rows = await client.query(
        "CREATE code_region SET file_path = 'src/a.py', symbol_name = $s, "
        "start_line = 1, end_line = 5",
        {"s": symbol},
    )
    return str(rows[0]["id"])


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_partial_verdict_accepted_on_never_compliant_pair():
    """The happy path for anticipatory bindings: 'partial' writes cleanly."""
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        v = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_pre",
            verdict="partial",
            confidence="high",
            explanation="anchored for future implementation; not yet built",
        )

        resp = await handle_resolve_compliance(ctx, phase="drift", verdicts=[v])

        assert len(resp.rejected) == 0
        assert len(resp.accepted) == 1
        assert resp.accepted[0].verdict == "partial"

        cached = await get_compliance_verdict(client, decision_id, region_id, "hash_pre")
        assert cached is not None
        assert cached["verdict"] == "partial"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_drifted_rejected_when_no_prior_compliant_row():
    """The invariant: cannot drift from a state you never reached.

    The rejection payload must be structured enough that a caller-LLM can
    downgrade to 'partial' on the next call without round-tripping with the
    user. We pin the specific fields the bicameral-sync skill relies on.
    """
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        v = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_x",
            verdict="drifted",
            confidence="high",
            explanation="caller-LLM's pre-#405 habit",
        )

        resp = await handle_resolve_compliance(ctx, phase="drift", verdicts=[v])

        assert len(resp.accepted) == 0
        assert len(resp.rejected) == 1
        rej = resp.rejected[0]
        assert rej.reason == "state_transition_invalid"
        assert rej.decision_id == decision_id
        assert rej.region_id == region_id
        assert rej.attempted_verdict == "drifted"
        assert "partial" in rej.allowed_verdicts
        assert "compliant" in rej.allowed_verdicts
        assert "drifted" not in rej.allowed_verdicts
        # prior history is empty for a never-touched pair.
        assert rej.prior_history_summary == {
            "compliant": 0,
            "drifted": 0,
            "partial": 0,
            "not_relevant": 0,
        }

        # The rejected verdict must NOT be persisted — that's the whole point.
        rows = await client.query("SELECT id FROM compliance_check")
        assert len(rows) == 0
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_drifted_accepted_after_prior_compliant_exists():
    """Real regression flow — once compliant, future drifted is allowed."""
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        # Step 1 — baseline: code matches the decision.
        first = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_v1",
            verdict="compliant",
            confidence="high",
            explanation="initial implementation matches the decision",
        )
        resp1 = await handle_resolve_compliance(ctx, phase="ingest", verdicts=[first])
        assert len(resp1.accepted) == 1

        # Step 2 — code changes, the new hash is no longer compliant. The
        # prior compliant row on a DIFFERENT content_hash unlocks 'drifted'
        # on the same (decision, region) pair.
        regressed = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_v2",
            verdict="drifted",
            confidence="high",
            explanation="threshold changed in v2",
        )
        resp2 = await handle_resolve_compliance(ctx, phase="drift", verdicts=[regressed])

        assert len(resp2.rejected) == 0
        assert len(resp2.accepted) == 1
        assert resp2.accepted[0].verdict == "drifted"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_caller_can_recover_by_downgrading_to_partial():
    """Replay-after-rejection contract: the caller resubmits the same batch
    with 'partial' in place of 'drifted' and the server accepts it. No re-
    binding required — this is the whole reason the rejection is structured.
    """
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        # First call — rejected.
        bad = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_a",
            verdict="drifted",
            confidence="medium",
            explanation="never-compliant region",
        )
        rej_resp = await handle_resolve_compliance(ctx, phase="drift", verdicts=[bad])
        assert rej_resp.rejected[0].reason == "state_transition_invalid"

        # Second call — same payload, downgraded.
        good = ComplianceVerdict(
            decision_id=decision_id,
            region_id=region_id,
            content_hash="hash_a",
            verdict="partial",
            confidence="medium",
            explanation="downgrade per rejection guidance",
        )
        ok_resp = await handle_resolve_compliance(ctx, phase="drift", verdicts=[good])
        assert len(ok_resp.rejected) == 0
        assert len(ok_resp.accepted) == 1
        assert ok_resp.accepted[0].verdict == "partial"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_state_transition_rejection_does_not_block_other_verdicts_in_batch():
    """A drifted-on-never-compliant rejection must not poison the batch —
    other verdicts in the same call still land."""
    ctx, client = await _fresh_stub_ctx()
    try:
        d1 = await _seed_decision(client, description="ok decision")
        d2 = await _seed_decision(client, description="anticipatory decision")
        r1 = await _seed_region(client, symbol="implemented_fn")
        r2 = await _seed_region(client, symbol="future_fn")

        good = ComplianceVerdict(
            decision_id=d1,
            region_id=r1,
            content_hash="hash_ok",
            verdict="compliant",
            confidence="high",
            explanation="works",
        )
        bad = ComplianceVerdict(
            decision_id=d2,
            region_id=r2,
            content_hash="hash_bad",
            verdict="drifted",
            confidence="high",
            explanation="never compliant",
        )

        resp = await handle_resolve_compliance(ctx, phase="drift", verdicts=[good, bad])

        assert len(resp.accepted) == 1
        assert resp.accepted[0].decision_id == d1
        assert len(resp.rejected) == 1
        assert resp.rejected[0].decision_id == d2
        assert resp.rejected[0].reason == "state_transition_invalid"
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_compliance_history_summary_counts_each_verdict():
    """The helper that powers prior_history_summary must surface counts for
    every verdict so the caller-LLM sees the full audit trail in the rejection."""
    ctx, client = await _fresh_stub_ctx()
    try:
        decision_id = await _seed_decision(client)
        region_id = await _seed_region(client)

        # Seed two compliant rows + one partial row at different hashes.
        for hash_, verdict in [
            ("h1", "compliant"),
            ("h2", "compliant"),
            ("h3", "partial"),
        ]:
            await client.execute(
                "CREATE compliance_check SET decision_id = $d, region_id = $r, "
                "content_hash = $h, verdict = $v, confidence = 'high', "
                "explanation = '', phase = 'ingest'",
                {"d": decision_id, "r": region_id, "h": hash_, "v": verdict},
            )

        summary = await compliance_history_summary(client, decision_id, region_id)
        assert summary == {
            "compliant": 2,
            "drifted": 0,
            "partial": 1,
            "not_relevant": 0,
        }
    finally:
        await client.close()
