"""HITL surfaces in preflight — sociable tests for #358.

Pins the contract that ``context_pending_ready`` actually surfaces
decisions in the preflight response. Pre-#358, the underlying
``get_context_for_ready_decisions`` query hardcoded ``status="context_pending"``
in its returned dicts, which violated ``BriefDecision.status`` (Literal of
``reflected | drifted | pending | ungrounded``) — the handler's outer
try/except at ``handlers/preflight.py:783`` swallowed the
``ValidationError`` silently and the field always returned empty in
production. The bug shipped silently because every test that exercised
this code path was a solitary mock that returned a valid status,
short-circuiting the validation. This file is the sociable counterweight.

Companion to the eval-harness ``FF4_hitl_topic_independent`` row in
``tests/eval/preflight_dataset.jsonl`` which pins the end-to-end flow.
These two tests check the ledger query layer directly — defense in
depth.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import (
    get_context_for_ready_decisions,
    relate_context_for,
)
from ledger.schema import init_schema, migrate

# Status values BriefDecision.status accepts. Source of truth for the
# Literal is contracts.py::BriefDecision; keep this constant in sync if
# the Literal is ever widened.
_VALID_BRIEF_DECISION_STATUSES = {"reflected", "drifted", "pending", "ungrounded"}

_NS_COUNTER = 0


async def _fresh_client() -> LedgerClient:
    """Build a fresh memory:// SurrealDB with schema migrated."""
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"hitl_{_NS_COUNTER}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    return client


async def _seed_context_pending_decision_with_confirmed_edge(
    client: LedgerClient,
    description: str = "Outstanding context-pending decision",
) -> str:
    """Seed the exact ledger state ``get_context_for_ready_decisions`` filters for.

    Returns the decision_id so tests can assert on it.
    """
    decision_rows = await client.query(
        "CREATE decision SET description=$d, status=$st, "
        "signoff={state: 'context_pending'}, "
        "source_type='test', source_ref='hitl_test'",
        {"d": description, "st": "pending"},
    )
    decision_id = str(decision_rows[0]["id"])

    span_rows = await client.query(
        "CREATE input_span SET text=$t, source_type='test', "
        "source_ref='hitl_test', speakers=[], meeting_date=''",
        {"t": "seed span"},
    )
    span_id = str(span_rows[0]["id"])

    await relate_context_for(client, span_id, decision_id, state="confirmed")
    return decision_id


async def test_get_context_for_ready_decisions_returns_brief_decision_compatible_status():
    """#358 root-cause pin: the function used to hardcode
    status='context_pending' which is NOT in BriefDecision.status's
    Literal set. Verify the returned status is now compatible.
    """
    client = await _fresh_client()
    try:
        await _seed_context_pending_decision_with_confirmed_edge(client)

        rows = await get_context_for_ready_decisions(client)

        assert len(rows) == 1, (
            f"expected exactly one context-pending row, got {len(rows)}: {rows!r}"
        )
        row_status = rows[0].get("status")
        assert row_status in _VALID_BRIEF_DECISION_STATUSES, (
            f"#358 regression: status={row_status!r} is not in "
            f"BriefDecision.status's Literal set ({_VALID_BRIEF_DECISION_STATUSES}). "
            f"This is the exact bug-class that hid in production for months — "
            f"the handler's try/except at handlers/preflight.py:783 would catch "
            f"the ValidationError and silently drop the row."
        )
    finally:
        await client.close()


async def test_get_context_for_ready_decisions_preserves_underlying_decision_status():
    """The returned status must reflect the decision's actual code-compliance
    state (pending / reflected / drifted / ungrounded), not be overridden
    to a signoff-state-like value. Otherwise the caller can't reason about
    code-compliance independently of signoff state.
    """
    client = await _fresh_client()
    try:
        decision_rows = await client.query(
            "CREATE decision SET description=$d, status='pending', "
            "signoff={state: 'context_pending'}, "
            "source_type='test', source_ref='hitl_test'",
            {"d": "explicit pending status"},
        )
        decision_id = str(decision_rows[0]["id"])
        span_rows = await client.query(
            "CREATE input_span SET text='seed', source_type='test', "
            "source_ref='hitl_test', speakers=[], meeting_date=''"
        )
        span_id = str(span_rows[0]["id"])
        await relate_context_for(client, span_id, decision_id, state="confirmed")

        rows = await get_context_for_ready_decisions(client)
        assert rows[0]["status"] == "pending", (
            f"expected status to mirror decision.status ('pending'), got {rows[0]['status']!r}"
        )
        # signoff_state is the surface for context_pending — keep it in
        # the signoff payload, NOT in the status field.
        sf = rows[0].get("signoff")
        assert isinstance(sf, dict) and sf.get("state") == "context_pending", (
            f"signoff.state should still be 'context_pending'; got signoff={sf!r}"
        )
    finally:
        await client.close()


async def test_handle_preflight_surfaces_context_pending_ready_end_to_end(monkeypatch):
    """End-to-end pin: with the #358 fix in place, the preflight response's
    ``context_pending_ready`` field actually contains the seeded decision.

    Pre-#358 this field was always empty in production because the handler's
    try/except at handlers/preflight.py:783 swallowed the
    BriefDecision ValidationError. With status now compatible, the field
    populates as designed.
    """
    import handlers.preflight as pf
    import handlers.sync_middleware as sm

    # Narrow seam: keep auto-sync stubbed so it doesn't link_commit against
    # the working tree. Same pattern as test_preflight_dedup_v2.py.
    monkeypatch.setattr(sm, "ensure_ledger_synced", AsyncMock(return_value=None))
    monkeypatch.setattr(pf, "_should_show_product_stage", lambda: False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_MUTE", raising=False)

    client = await _fresh_client()
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    try:
        await _seed_context_pending_decision_with_confirmed_edge(
            client, description="Outstanding context-pending in unrelated area"
        )

        ctx = SimpleNamespace(ledger=adapter, guided_mode=False, _sync_state={})

        response = await pf.handle_preflight(
            ctx=ctx,
            topic="something completely unrelated to the seeded decision",
            file_paths=[],
        )

        # The whole point: this surface must NOT be silently empty anymore.
        assert len(response.context_pending_ready) == 1, (
            f"#358 regression: expected 1 context_pending_ready entry, got "
            f"{len(response.context_pending_ready)}. If this is 0, the handler "
            f"is silently swallowing a BriefDecision ValidationError again — "
            f"check handlers/preflight.py:783 logs at DEBUG level."
        )
        entry = response.context_pending_ready[0]
        assert entry.status in _VALID_BRIEF_DECISION_STATUSES
        assert entry.signoff_state == "context_pending", (
            f"signoff_state should carry the context_pending signal; "
            f"got signoff_state={entry.signoff_state!r}"
        )

        # HITL is intentionally topic-independent — fired must be True
        # because of this surface alone (no region match, no guided mode).
        assert response.fired is True, (
            f"HITL surface alone should make fired=True; got fired={response.fired} "
            f"reason={response.reason!r}"
        )
    finally:
        await client.close()


pytestmark = pytest.mark.asyncio
