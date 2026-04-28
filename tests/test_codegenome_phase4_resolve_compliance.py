"""Phase 4 / Phase 4 (#61) — resolve_compliance handler integration.

Covers the ``handlers.resolve_compliance`` extension that persists
the optional ``semantic_status`` + ``evidence_refs`` from
``ComplianceVerdict`` payloads into the ``compliance_check`` row.

End-to-end: payload → handler → ledger query → row inspection.
"""

from __future__ import annotations

import os

import pytest

from contracts import ComplianceVerdict
from handlers.resolve_compliance import handle_resolve_compliance
from ledger.client import LedgerClient
from ledger.queries import upsert_decision, upsert_code_region, relate_binds_to
from ledger.schema import init_schema, migrate

pytestmark = pytest.mark.phase2


@pytest.fixture
async def ctx_with_seed():
    """Build a minimal ctx with a real ledger + seeded decision/region."""
    surreal_url = os.getenv("SURREAL_URL", "memory://")
    client = LedgerClient(surreal_url)
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)

    decision_id = await upsert_decision(
        client,
        description="Apply 10% discount on orders >= $100",
        rationale="", source_type="transcript", source_ref="m1",
        meeting_date="2026-01-01", speakers=["a@b.c"],
    )
    region_id = await upsert_code_region(
        client, file_path="pricing.py", symbol_name="discount",
        start_line=1, end_line=10, repo="test", content_hash="h-1",
    )
    await relate_binds_to(client, decision_id, region_id, confidence=0.9)

    # Minimal ctx surface that handle_resolve_compliance uses.
    class FakeCtx:
        pass
    ctx = FakeCtx()

    class _LedgerWrapper:
        _client = client
        async def connect(self): return None
        async def get_decision_description(self, did): return "x"

    ctx.ledger = _LedgerWrapper()
    ctx.repo_path = "/tmp/repo"
    yield ctx, client, decision_id, region_id
    await client.close()


# ── Persistence tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_caller_verdict_with_semantic_status_persists(
    ctx_with_seed,
) -> None:
    ctx, client, decision_id, region_id = ctx_with_seed
    verdict = ComplianceVerdict(
        decision_id=decision_id, region_id=region_id,
        content_hash="h-1", verdict="compliant",
        confidence="high", explanation="ok",
        semantic_status="semantically_preserved",
        evidence_refs=["caller:reviewed"],
    )
    await handle_resolve_compliance(ctx, "drift", [verdict])
    rows = await client.query(
        "SELECT verdict, semantic_status, evidence_refs FROM compliance_check "
        f"WHERE decision_id = '{decision_id}'",
    )
    assert rows
    assert rows[0]["verdict"] == "compliant"
    assert rows[0]["semantic_status"] == "semantically_preserved"
    assert rows[0]["evidence_refs"] == ["caller:reviewed"]


@pytest.mark.asyncio
async def test_caller_verdict_without_semantic_status_persists_as_null(
    ctx_with_seed,
) -> None:
    """Legacy callers (no semantic_status / evidence_refs) → row has
    NULL / [] defaults. Backward-compatible."""
    ctx, client, decision_id, region_id = ctx_with_seed
    verdict = ComplianceVerdict(
        decision_id=decision_id, region_id=region_id,
        content_hash="h-1", verdict="compliant",
        confidence="high", explanation="ok",
    )
    await handle_resolve_compliance(ctx, "drift", [verdict])
    rows = await client.query(
        "SELECT semantic_status, evidence_refs FROM compliance_check "
        f"WHERE decision_id = '{decision_id}'",
    )
    assert rows
    assert rows[0].get("semantic_status") in (None, "NONE")
    assert rows[0]["evidence_refs"] == []


@pytest.mark.asyncio
async def test_evidence_refs_round_trip_through_caller_verdict(
    ctx_with_seed,
) -> None:
    ctx, client, decision_id, region_id = ctx_with_seed
    refs = ["score:0.92", "signature:1.00", "neighbors:0.97"]
    verdict = ComplianceVerdict(
        decision_id=decision_id, region_id=region_id,
        content_hash="h-1", verdict="compliant",
        confidence="high", explanation="ok",
        semantic_status="semantically_preserved",
        evidence_refs=refs,
    )
    await handle_resolve_compliance(ctx, "drift", [verdict])
    rows = await client.query(
        "SELECT evidence_refs FROM compliance_check "
        f"WHERE decision_id = '{decision_id}'",
    )
    assert rows[0]["evidence_refs"] == refs


@pytest.mark.asyncio
async def test_caller_verdict_invalid_semantic_status_rejected_at_pydantic(
    ctx_with_seed,
) -> None:
    """F2 regression at the contract layer — Pydantic refuses the
    dropped 'pre_classification_hint' value before the handler is
    invoked."""
    from pydantic import ValidationError
    ctx, _, decision_id, region_id = ctx_with_seed
    with pytest.raises(ValidationError):
        ComplianceVerdict(
            decision_id=decision_id, region_id=region_id,
            content_hash="h-1", verdict="compliant",
            confidence="high", explanation="ok",
            semantic_status="pre_classification_hint",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_resolve_compliance_response_echoes_semantic_status(
    ctx_with_seed,
) -> None:
    """``ResolveComplianceAccepted.semantic_status`` is set on the
    accepted entry when the caller provided one."""
    ctx, client, decision_id, region_id = ctx_with_seed
    verdict = ComplianceVerdict(
        decision_id=decision_id, region_id=region_id,
        content_hash="h-1", verdict="drifted",
        confidence="medium", explanation="real change",
        semantic_status="semantic_change",
        evidence_refs=["caller:override"],
    )
    response = await handle_resolve_compliance(ctx, "drift", [verdict])
    assert len(response.accepted) == 1
    assert response.accepted[0].semantic_status == "semantic_change"
