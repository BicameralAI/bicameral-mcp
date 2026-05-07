"""Phase 2 (#77) — write helper regression tests.

Exercises ``ledger.queries.update_decision_level`` directly against a
memory:// SurrealDB instance, covering happy path, defensive validation,
idempotency, and unknown-decision-id behaviour.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.queries import (
    DecisionNotFound,
    get_decision_level,
    update_decision_level,
    upsert_decision,
)
from ledger.schema import init_schema


@pytest.fixture
async def client():
    """Yield a connected memory:// LedgerClient with schema applied."""
    c = LedgerClient(url="memory://")
    await c.connect()
    await init_schema(c)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
async def decision_id(client):
    """Create a fresh decision row and return its full record id."""
    did = await upsert_decision(
        client,
        description="Members can pause their subscription for up to 90 days.",
        source_type="manual",
        source_ref="test-fixture",
    )
    assert did.startswith("decision:"), f"unexpected id shape: {did!r}"
    return did


# ── Happy path ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_decision_level_writes_value(client, decision_id):
    """Writing 'L2' is observable via get_decision_level."""
    await update_decision_level(client, decision_id, "L2")
    level = await get_decision_level(client, decision_id)
    assert level == "L2"


# ── Defensive validation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_decision_level_rejects_invalid_value(client, decision_id):
    """Writing 'L4' raises ValueError before any DB query runs."""
    with pytest.raises(ValueError, match="invalid level"):
        await update_decision_level(client, decision_id, "L4")
    # Ensure nothing got written
    assert await get_decision_level(client, decision_id) is None


@pytest.mark.asyncio
async def test_update_decision_level_rejects_malformed_id(client):
    """Malformed decision_id raises ValueError (audit S1 defense-in-depth)."""
    with pytest.raises(ValueError, match="invalid decision_id shape"):
        await update_decision_level(client, "foo bar", "L2")
    with pytest.raises(ValueError, match="invalid decision_id shape"):
        await update_decision_level(client, "not_a_record_id", "L2")
    with pytest.raises(ValueError, match="invalid decision_id shape"):
        await update_decision_level(client, "decision:abc;DROP TABLE x;", "L2")


# ── Idempotency ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_decision_level_idempotent(client, decision_id):
    """Writing the same level twice is a no-op (no errors, value unchanged)."""
    await update_decision_level(client, decision_id, "L1")
    await update_decision_level(client, decision_id, "L1")
    assert await get_decision_level(client, decision_id) == "L1"


# ── Unknown decision id ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_decision_level_unknown_decision_id(client):
    """Writing to a syntactically valid but nonexistent decision_id raises
    DecisionNotFound with the bad id."""
    with pytest.raises(DecisionNotFound) as exc_info:
        await update_decision_level(client, "decision:does_not_exist", "L2")
    assert "decision:does_not_exist" in str(exc_info.value)
