"""Phase 3 (#77) — set_decision_level handler tests."""

from __future__ import annotations

import pytest

from handlers.list_unclassified_decisions import handle_list_unclassified_decisions
from handlers.set_decision_level import handle_set_decision_level
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.queries import upsert_decision


class _Ctx:
    def __init__(self, ledger):
        self.ledger = ledger


@pytest.fixture
async def ctx_with_ledger(monkeypatch):
    monkeypatch.setenv("USE_REAL_LEDGER", "1")
    monkeypatch.setenv("SURREAL_URL", "memory://")
    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    try:
        yield _Ctx(adapter)
    finally:
        await adapter._client.close()


async def _seed_decision(ctx, description: str) -> str:
    return await upsert_decision(
        ctx.ledger._client,
        description=description,
        source_type="manual",
        source_ref="test",
    )


@pytest.mark.asyncio
async def test_set_decision_level_writes_value(ctx_with_ledger):
    ctx = ctx_with_ledger
    did = await _seed_decision(ctx, "Members can pause their subscription.")

    response = await handle_set_decision_level(ctx, decision_id=did, level="L1")
    assert response.ok is True
    assert response.decision_id == did
    assert response.level == "L1"
    assert response.error is None

    # Subsequent list_unclassified call must not include this row
    list_resp = await handle_list_unclassified_decisions(ctx)
    assert all(p.decision_id != did for p in list_resp.proposals)


@pytest.mark.asyncio
async def test_set_decision_level_invalid_level_returns_error_response(ctx_with_ledger):
    ctx = ctx_with_ledger
    did = await _seed_decision(ctx, "Members can pause their subscription.")

    response = await handle_set_decision_level(ctx, decision_id=did, level="L4")
    assert response.ok is False
    assert response.decision_id == did
    assert response.level is None
    assert response.error is not None
    assert "invalid level" in response.error.lower()

    # Confirm no write happened — row still unclassified
    list_resp = await handle_list_unclassified_decisions(ctx)
    assert any(p.decision_id == did for p in list_resp.proposals)


@pytest.mark.asyncio
async def test_set_decision_level_unknown_id_returns_error_response(ctx_with_ledger):
    ctx = ctx_with_ledger
    response = await handle_set_decision_level(
        ctx,
        decision_id="decision:does_not_exist",
        level="L1",
    )
    assert response.ok is False
    assert response.error is not None
    assert "not found" in response.error.lower() or "does_not_exist" in response.error
