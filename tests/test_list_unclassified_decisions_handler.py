"""Phase 3 (#77) — list_unclassified_decisions handler tests."""

from __future__ import annotations

import pytest

from handlers.list_unclassified_decisions import handle_list_unclassified_decisions
from ledger.adapter import SurrealDBLedgerAdapter
from ledger.queries import update_decision_level, upsert_decision


class _Ctx:
    """Minimal BicameralContext stand-in for handler tests."""

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


async def _seed_decision(ctx, description: str, level: str | None = None) -> str:
    did = await upsert_decision(
        ctx.ledger._client,
        description=description,
        source_type="manual",
        source_ref="test",
    )
    if level is not None:
        await update_decision_level(ctx.ledger._client, did, level)
    return did


@pytest.mark.asyncio
async def test_list_unclassified_returns_only_null_rows(ctx_with_ledger):
    ctx = ctx_with_ledger
    # 3 NULL + 2 classified
    await _seed_decision(ctx, "Members can pause their subscription.")
    await _seed_decision(ctx, "Use Redis-backed sessions for scaling.")
    await _seed_decision(ctx, "Users can export data as CSV.")
    await _seed_decision(ctx, "Already L1 row.", level="L1")
    await _seed_decision(ctx, "Already L2 row.", level="L2")

    response = await handle_list_unclassified_decisions(ctx)
    assert response.total_count == 3
    assert len(response.proposals) == 3
    descs = sorted(p.description for p in response.proposals)
    assert descs == sorted(
        [
            "Members can pause their subscription.",
            "Use Redis-backed sessions for scaling.",
            "Users can export data as CSV.",
        ]
    )


@pytest.mark.asyncio
async def test_list_unclassified_filters_by_decision_ids(ctx_with_ledger):
    ctx = ctx_with_ledger
    d1 = await _seed_decision(ctx, "Members can pause their subscription.")
    d2 = await _seed_decision(ctx, "Use Redis-backed sessions for scaling.")
    await _seed_decision(ctx, "Users can export data as CSV.")  # not in filter

    response = await handle_list_unclassified_decisions(ctx, decision_ids=[d1, d2])
    assert response.total_count == 2
    returned_ids = {p.decision_id for p in response.proposals}
    assert returned_ids == {d1, d2}


@pytest.mark.asyncio
async def test_list_unclassified_includes_proposed_level_and_rationale(ctx_with_ledger):
    ctx = ctx_with_ledger
    await _seed_decision(ctx, "Members can pause their subscription for up to 90 days.")
    await _seed_decision(ctx, "Use Redis-backed session storage for horizontal scaling.")

    response = await handle_list_unclassified_decisions(ctx)
    proposals_by_desc = {p.description: p for p in response.proposals}
    p_l1 = proposals_by_desc["Members can pause their subscription for up to 90 days."]
    p_l2 = proposals_by_desc["Use Redis-backed session storage for horizontal scaling."]
    assert p_l1.proposed_level == "L1"
    assert p_l1.rationale  # non-empty
    assert p_l2.proposed_level == "L2"
    assert p_l2.rationale


@pytest.mark.asyncio
async def test_list_unclassified_marks_low_confidence(ctx_with_ledger):
    ctx = ctx_with_ledger
    # Generic text with no L1/L2/L3 signal -> defaults to L3 with low confidence
    await _seed_decision(ctx, "stuff happens here")
    # And one with strong L1 signal
    await _seed_decision(ctx, "Users can pause their subscription.")

    response = await handle_list_unclassified_decisions(ctx)
    proposals_by_desc = {p.description: p for p in response.proposals}
    assert proposals_by_desc["stuff happens here"].confidence == "low"
    assert proposals_by_desc["Users can pause their subscription."].confidence == "high"
