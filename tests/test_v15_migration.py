"""Phase 1 (#109) — v14 → v15 migration: decision.governance field."""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import SCHEMA_VERSION, init_schema, migrate


async def _fresh_client() -> LedgerClient:
    c = LedgerClient(url="memory://", ns="bicameral_test", db="ledger_v15_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v15_migration_adds_governance_field() -> None:
    """A migrated DB exposes the optional governance field on decision."""
    c = await _fresh_client()
    try:
        # Schema must be at the current version (>= 15) after migrate.
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 15

        # Inserting a decision without governance must succeed; the
        # field reads back as None (NONE in SurrealDB).
        await c.query(
            "CREATE decision SET description = $d, source_type = $st, "
            "source_ref = $sr, status = 'ungrounded', canonical_id = 'cid-v15-1'",
            {"d": "v15 governance probe", "st": "manual", "sr": "v15-test"},
        )
        rows = await c.query(
            "SELECT description, governance FROM decision "
            "WHERE description = 'v15 governance probe'"
        )
        assert rows
        assert rows[0]["description"] == "v15 governance probe"
        # FLEXIBLE option<object> default NONE: missing key OR explicit None.
        gov = rows[0].get("governance")
        assert gov is None or gov == {}

        # Writing a governance object preserves nested keys (FLEXIBLE).
        await c.query(
            "UPDATE decision SET governance = $g WHERE description = 'v15 governance probe'",
            {
                "g": {
                    "decision_class": "security",
                    "risk_class": "critical",
                    "escalation_class": "notify_supervisor_allowed",
                    "protected_component": True,
                }
            },
        )
        rows = await c.query(
            "SELECT governance FROM decision WHERE description = 'v15 governance probe'"
        )
        gov = rows[0]["governance"]
        assert gov is not None
        assert gov.get("decision_class") == "security"
        assert gov.get("risk_class") == "critical"
        assert gov.get("escalation_class") == "notify_supervisor_allowed"
        assert gov.get("protected_component") is True
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v15_migration_idempotent() -> None:
    """Running migrate() twice is a no-op."""
    c = await _fresh_client()
    try:
        # Already at SCHEMA_VERSION after _fresh_client().
        await migrate(c, allow_destructive=True)
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] == SCHEMA_VERSION
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_existing_decisions_readable_after_v15() -> None:
    """Pre-v15 decisions survive the migration; governance defaults to None."""
    c = await _fresh_client()
    try:
        # Simulate a pre-v15 row: insert a decision with no governance set.
        await c.query(
            "CREATE decision SET description = $d, source_type = $st, "
            "source_ref = $sr, status = 'ungrounded', canonical_id = 'cid-v15-2'",
            {"d": "pre-v15 row", "st": "manual", "sr": "v15-pre"},
        )
        rows = await c.query(
            "SELECT description, governance FROM decision WHERE description = 'pre-v15 row'"
        )
        assert rows
        # No governance set → default behaviour: None / missing.
        gov = rows[0].get("governance")
        assert gov is None or gov == {} or gov == "NONE"
    finally:
        await c.close()
