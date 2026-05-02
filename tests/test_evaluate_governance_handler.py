"""Phase 3 (#108) — bicameral.evaluate_governance handler unit tests."""

from __future__ import annotations

import pytest

from handlers.evaluate_governance import handle_evaluate_governance
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate


class _StubInner:
    def __init__(self, client: LedgerClient) -> None:
        self._client = client


class _StubLedger:
    def __init__(self, client: LedgerClient) -> None:
        self._inner = _StubInner(client)


class _StubCtx:
    def __init__(self, ledger: _StubLedger) -> None:
        self.ledger = ledger


async def _fresh_client_and_ctx() -> tuple[LedgerClient, _StubCtx]:
    c = LedgerClient(url="memory://", ns="bicameral_test", db="ledger_evgov")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    ctx = _StubCtx(_StubLedger(c))
    return c, ctx


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_handler_returns_engine_result_for_finding() -> None:
    """Existing decision_id → response carries finding with policy_result."""
    client, ctx = await _fresh_client_and_ctx()
    try:
        rows = await client.query(
            "CREATE decision SET description = $d, source_type = 'manual', "
            "source_ref = 'evgov-1', status = 'ungrounded', "
            "canonical_id = 'cid-evgov-1', "
            "signoff = {state: 'ratified'}, "
            "decision_level = 'L2'",
            {"d": "evgov handler test"},
        )
        decision_id = str(rows[0]["id"])
        resp = await handle_evaluate_governance(ctx, decision_id=decision_id)
        assert resp.error is None
        assert resp.finding is not None
        assert resp.finding.decision_id == decision_id
        assert resp.finding.policy_result is not None
        assert resp.finding.decision_class == "architecture"  # L2 default
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_handler_unknown_decision_id_returns_error_response() -> None:
    """Unknown decision_id → error='unknown_decision_id', finding=None."""
    client, ctx = await _fresh_client_and_ctx()
    try:
        resp = await handle_evaluate_governance(ctx, decision_id="decision:does_not_exist")
        assert resp.error == "unknown_decision_id"
        assert resp.finding is None
    finally:
        await client.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_handler_handles_governance_metadata_default() -> None:
    """Decision with no governance row → metadata derived from decision_level."""
    client, ctx = await _fresh_client_and_ctx()
    try:
        rows = await client.query(
            "CREATE decision SET description = $d, source_type = 'manual', "
            "source_ref = 'evgov-2', status = 'ungrounded', "
            "canonical_id = 'cid-evgov-2', "
            "signoff = {state: 'ratified'}",
            {"d": "evgov default test"},
        )
        decision_id = str(rows[0]["id"])
        resp = await handle_evaluate_governance(ctx, decision_id=decision_id)
        assert resp.error is None
        assert resp.finding is not None
        # No decision_level → falls back to L1 defaults: product_behavior.
        assert resp.finding.decision_class == "product_behavior"
        assert resp.finding.policy_result is not None
    finally:
        await client.close()
