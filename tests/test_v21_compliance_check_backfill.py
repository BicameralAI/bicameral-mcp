"""v20 → v21 migration: backfill compliance_check rows for pre-verdict reflected decisions.

Regression test for the hotfix: the original migration used sentinel values
``confidence='migrated'`` and ``phase='migration'`` which were rejected by
their respective ASSERTs at runtime, blocking ingest for any user whose
ledger crossed the v20→v21 boundary with non-empty reflected decisions.

This test seeds a reflected decision with a binding (the trigger state)
and runs ``_migrate_v20_to_v21`` directly, asserting that the synthetic
compliance_check row lands successfully (no SchemaError) with the fix's
remapped enum values (``confidence='low'``, ``phase='regrounding'``).

Sociable test — real SurrealDB adapter over ``memory://``; real schema
init + migrate; no mocks on the ledger surface.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import _migrate_v20_to_v21, init_schema, migrate

_NS_COUNTER = 0


async def _fresh_client() -> LedgerClient:
    global _NS_COUNTER
    _NS_COUNTER += 1
    c = LedgerClient(
        url="memory://",
        ns=f"v21_test_{_NS_COUNTER}",
        db="ledger_v21_test",
    )
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_reflected_decision_with_binding(
    c: LedgerClient, *, description: str
) -> tuple[str, str]:
    """Create a 'reflected' decision bound to a code region. Returns (decision_id, region_id).

    Mirrors the pre-verdict-era state the migration is designed to repair —
    decision is `reflected` via legacy hash-comparison, but no compliance_check
    row exists. After the migration, exactly one synthetic compliance_check
    should land.
    """
    drows = await c.query(
        "CREATE decision SET description = $d, source_type = 'manual', "
        "canonical_id = $cid, status = 'reflected'",
        {"d": description, "cid": f"cid-{description}"},
    )
    drow = drows[0]
    rid = drow.get("id")
    decision_id = (
        f"decision:{rid.get('id', rid)}" if isinstance(rid, dict) else str(rid)
    )

    rrows = await c.query(
        "CREATE code_region SET file_path = $f, start_line = 1, end_line = 5, "
        "symbol_name = $s, content_hash = $h",
        {"f": "src/x.py", "s": "fn_x", "h": "abc123hashvalue"},
    )
    rrow = rrows[0]
    rrid = rrow.get("id")
    region_id = (
        f"code_region:{rrid.get('id', rrid)}" if isinstance(rrid, dict) else str(rrid)
    )

    await c.query(
        f"RELATE {decision_id}->binds_to->{region_id} "
        "SET content_hash = $h, confidence = 1.0",
        {"h": "abc123hashvalue"},
    )
    return decision_id, region_id


async def test_v20_to_v21_migration_does_not_raise_on_reflected_with_binding() -> None:
    """Original bug: migration set confidence='migrated' and phase='migration',
    both rejected by their ASSERTs, raising SurrealDB ERROR. Fix maps to
    ``confidence='low'`` and ``phase='regrounding'`` (valid enum values).
    Test passes iff the migration completes without raising.
    """
    c = await _fresh_client()
    await _seed_reflected_decision_with_binding(c, description="kyc tier1 ssn-last4")

    # Should complete cleanly; without the hotfix this raises a SurrealDB
    # schema-violation error on the CREATE compliance_check statement.
    await _migrate_v20_to_v21(c)


async def test_v20_to_v21_migration_writes_one_compliance_check_per_binding() -> None:
    """Verify the migration's actual output: a synthetic compliance_check
    row exists for the reflected decision after the migration runs, with
    verdict='compliant' and the matching content_hash.
    """
    c = await _fresh_client()
    decision_id, region_id = await _seed_reflected_decision_with_binding(
        c, description="velocity check 3-per-5"
    )

    await _migrate_v20_to_v21(c)

    rows = await c.query(
        "SELECT verdict, confidence, phase, content_hash, explanation "
        "FROM compliance_check WHERE decision_id = $d",
        {"d": decision_id},
    )
    assert len(rows) == 1, f"expected 1 compliance_check row, got {len(rows)}"
    row = rows[0]
    assert row["verdict"] == "compliant"
    assert row["confidence"] == "low"  # hotfix mapping
    assert row["phase"] == "regrounding"  # hotfix mapping
    assert row["content_hash"] == "abc123hashvalue"
    assert "backfilled by v20→v21" in row["explanation"]


async def test_v20_to_v21_migration_is_idempotent() -> None:
    """Idempotency: running the migration twice does not create duplicate
    compliance_check rows for the same decision.
    """
    c = await _fresh_client()
    decision_id, _ = await _seed_reflected_decision_with_binding(
        c, description="velocity check 3-per-5"
    )

    await _migrate_v20_to_v21(c)
    await _migrate_v20_to_v21(c)

    rows = await c.query(
        "SELECT id FROM compliance_check WHERE decision_id = $d",
        {"d": decision_id},
    )
    assert len(rows) == 1


async def test_v20_to_v21_migration_skips_decision_with_existing_check() -> None:
    """A reflected decision that ALREADY has a compliance_check row is not
    re-backfilled by the migration.
    """
    c = await _fresh_client()
    decision_id, region_id = await _seed_reflected_decision_with_binding(
        c, description="cross border wire fee"
    )

    # Pre-existing compliance_check (e.g., written by the modern verdict gate)
    await c.execute(
        "CREATE compliance_check SET decision_id = $d, region_id = $r, "
        "content_hash = $h, verdict = 'compliant', confidence = 'high', "
        "explanation = 'real verdict, not migration backfill', "
        "phase = 'ingest', pruned = false, ephemeral = false",
        {"d": decision_id, "r": region_id, "h": "abc123hashvalue"},
    )

    await _migrate_v20_to_v21(c)

    rows = await c.query(
        "SELECT confidence, explanation FROM compliance_check WHERE decision_id = $d",
        {"d": decision_id},
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == "high"  # original row preserved, not overwritten
    assert "real verdict" in rows[0]["explanation"]


async def test_v20_to_v21_migration_noop_when_no_reflected_decisions() -> None:
    """Migration short-circuits when there are no reflected decisions to backfill."""
    c = await _fresh_client()
    # No decisions seeded.
    await _migrate_v20_to_v21(c)

    rows = await c.query("SELECT id FROM compliance_check")
    assert rows == []
