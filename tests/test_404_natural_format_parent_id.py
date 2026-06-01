"""Sociable tests for #404 Phase 1.

Verifies that the natural-format ingest API (a) honors a caller-supplied
``parent_decision_id`` (previously silently dropped — only the internal mapping
format honored it) and (b) lets an explicit ``decision_level`` override the
source-type heuristic.

Real ``SurrealDBLedgerAdapter`` over ``memory://`` (via ``BicameralContext``) and
the real ``_handle_ingest_impl`` — no ``MagicMock`` of ctx or ledger, per the
CLAUDE.md sociable-testing rule. The parent_decision_id test is load-bearing:
before ``_normalize_payload`` threads the field it never reaches the mapping, the
adapter writes ``NONE``, and the read-back assertion fails — so the test proves
the threading, not a mock.
"""

from __future__ import annotations

import pytest

from context import BicameralContext
from handlers.ingest import _handle_ingest_impl


async def _get_client(ctx):
    """Idiom from tests/test_ephemeral_authoritative.py::_get_client."""
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()
    inner = getattr(ledger, "_inner", ledger)
    return inner._client


def _natural_payload(*, decision_level, parent_decision_id=None, source="agent_session"):
    decision: dict = {
        "description": "Redis-backed sessions for horizontal scale",
        "source_excerpt": "we moved sessions to Redis so checkout could scale",
        "decision_level": decision_level,
    }
    if parent_decision_id is not None:
        decision["parent_decision_id"] = parent_decision_id
    return {
        "query": "checkout scaling",
        "source": source,
        "title": "sess-404-phase1",
        "decisions": [decision],
    }


@pytest.fixture
def mem_ctx(monkeypatch):
    """A BicameralContext pinned to an in-memory ledger (never the real db)."""
    monkeypatch.setenv("SURREAL_URL", "memory://")
    return BicameralContext.from_env()


async def test_natural_format_honors_parent_decision_id(mem_ctx):
    """An L2 decision ingested via natural format with an explicit
    parent_decision_id persists that parent on the decision row."""
    payload = _natural_payload(decision_level="L2", parent_decision_id="decision:fake_parent_404")
    resp = await _handle_ingest_impl(mem_ctx, payload)
    assert resp.ingested, f"ingest failed: {resp}"

    decision_id = resp.created_decisions[0].decision_id
    client = await _get_client(mem_ctx)
    rows = await client.query(f"SELECT parent_decision_id FROM {decision_id} LIMIT 1")
    assert rows, "decision row not found"
    assert rows[0]["parent_decision_id"] == "decision:fake_parent_404"


async def test_explicit_decision_level_overrides_source_heuristic(mem_ctx):
    """source='agent_session' would classify L3 via the heuristic; an explicit
    decision_level='L1' in the payload must win (Acceptance #5, #340 precedence)."""
    payload = _natural_payload(decision_level="L1", source="agent_session")
    resp = await _handle_ingest_impl(mem_ctx, payload)
    assert resp.ingested, f"ingest failed: {resp}"

    decision_id = resp.created_decisions[0].decision_id
    client = await _get_client(mem_ctx)
    rows = await client.query(f"SELECT decision_level FROM {decision_id} LIMIT 1")
    assert rows, "decision row not found"
    assert rows[0]["decision_level"] == "L1"
