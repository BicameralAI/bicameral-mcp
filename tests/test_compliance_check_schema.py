"""Schema tests for compliance_check (v3) — LLM verification cache.

Validates Phase 1 of the unified compliance verification plan:
thoughts/shared/plans/2026-04-20-ingest-time-verification.md.

The compliance_check table is the cache layer that lets reads project
REFLECTED / DRIFTED / PENDING without calling an LLM. Cache key is
``(intent_id, region_id, content_hash)`` — the hash of the code shape
the caller LLM actually evaluated.

These tests pin the fields, the enum constraints, the defaults, and the
UNIQUE cache-key index. They run against memory:// for hermetic isolation.
"""
from __future__ import annotations

import pytest

from ledger.client import LedgerClient, LedgerError
from ledger.schema import SCHEMA_VERSION, init_schema, migrate


async def _fresh_client() -> LedgerClient:
    """Fresh in-memory SurrealDB with schema applied and migrations run."""
    c = LedgerClient(url="memory://", ns="bicameral_test", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c)
    return c


# ── Version stamp ────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_schema_version_is_3_after_migrate():
    """v2→v3 migration bumps schema_meta to 3 (confirms the new table shipped)."""
    assert SCHEMA_VERSION == 3, "code-level constant must be 3"
    c = await _fresh_client()
    try:
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows, "schema_meta row missing after migrate"
        assert rows[0]["version"] == 3
    finally:
        await c.close()


# ── Cache-key uniqueness ─────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_unique_cache_key_rejects_duplicate_tuple():
    """Same (intent_id, region_id, content_hash) twice must fail.

    This is the idempotency guarantee for resolve_compliance: replaying
    the same verdict batch is a no-op at the DB level.
    """
    c = await _fresh_client()
    try:
        await c.execute(
            "CREATE compliance_check SET intent_id = $i, region_id = $r, "
            "content_hash = $h, compliant = true, confidence = 'high', "
            "explanation = 'first', phase = 'ingest'",
            {"i": "intent:a", "r": "code_region:x", "h": "hash_abc"},
        )

        with pytest.raises(LedgerError, match="idx_cc_cache_key"):
            await c.execute(
                "CREATE compliance_check SET intent_id = $i, region_id = $r, "
                "content_hash = $h, compliant = false, confidence = 'low', "
                "explanation = 'second', phase = 'drift'",
                {"i": "intent:a", "r": "code_region:x", "h": "hash_abc"},
            )

        # Defensive: the first row's compliant=true must still be what's stored.
        rows = await c.query("SELECT compliant FROM compliance_check")
        assert len(rows) == 1
        assert rows[0]["compliant"] is True
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_unique_cache_key_allows_different_content_hash():
    """Same (intent, region) with DIFFERENT content_hash must succeed.

    When the region's code changes, a new row is written for the new hash
    while the old hash's row is retained (caches verdicts across reverts).
    """
    c = await _fresh_client()
    try:
        await c.execute(
            "CREATE compliance_check SET intent_id = $i, region_id = $r, "
            "content_hash = $h1, compliant = true, confidence = 'high', "
            "explanation = 'ok at h1', phase = 'ingest'",
            {"i": "intent:a", "r": "code_region:x", "h1": "hash_aaa"},
        )
        await c.execute(
            "CREATE compliance_check SET intent_id = $i, region_id = $r, "
            "content_hash = $h2, compliant = false, confidence = 'medium', "
            "explanation = 'broke at h2', phase = 'drift'",
            {"i": "intent:a", "r": "code_region:x", "h2": "hash_bbb"},
        )
        rows = await c.query(
            "SELECT content_hash FROM compliance_check "
            "WHERE intent_id = 'intent:a' AND region_id = 'code_region:x' "
            "ORDER BY content_hash"
        )
        assert len(rows) == 2
        assert {r["content_hash"] for r in rows} == {"hash_aaa", "hash_bbb"}
    finally:
        await c.close()


# ── Enum enforcement ─────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_confidence_enum_rejects_invalid_value():
    """confidence must be 'high' | 'medium' | 'low'."""
    c = await _fresh_client()
    try:
        with pytest.raises(LedgerError, match="confidence"):
            await c.execute(
                "CREATE compliance_check SET intent_id = 'intent:a', "
                "region_id = 'code_region:x', content_hash = 'h', "
                "compliant = true, confidence = 'very_high', "
                "explanation = '', phase = 'ingest'"
            )
        # No row should exist.
        rows = await c.query("SELECT id FROM compliance_check")
        assert len(rows) == 0
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_phase_enum_rejects_invalid_value():
    """phase must be one of the five reserved values."""
    c = await _fresh_client()
    try:
        with pytest.raises(LedgerError, match="phase"):
            await c.execute(
                "CREATE compliance_check SET intent_id = 'intent:a', "
                "region_id = 'code_region:x', content_hash = 'h', "
                "compliant = true, confidence = 'high', "
                "explanation = '', phase = 'mystery_phase'"
            )
        rows = await c.query("SELECT id FROM compliance_check")
        assert len(rows) == 0
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_phase_accepts_all_five_reserved_values():
    """ingest, drift, regrounding, supersession, divergence all accepted.

    Supersession and divergence have stub persistence in resolve_compliance
    but the schema accepts them so future plans don't need a migration.
    """
    c = await _fresh_client()
    try:
        for i, phase in enumerate(
            ("ingest", "drift", "regrounding", "supersession", "divergence")
        ):
            await c.execute(
                "CREATE compliance_check SET intent_id = $i, region_id = $r, "
                "content_hash = $h, compliant = true, confidence = 'high', "
                "explanation = '', phase = $p",
                {
                    "i": f"intent:{i}",
                    "r": f"code_region:{i}",
                    "h": f"hash_{i}",
                    "p": phase,
                },
            )
        rows = await c.query("SELECT phase FROM compliance_check")
        assert {r["phase"] for r in rows} == {
            "ingest",
            "drift",
            "regrounding",
            "supersession",
            "divergence",
        }
    finally:
        await c.close()


# ── Defaults ─────────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_defaults_phase_drift_explanation_empty_checked_at_now():
    """Omitted fields populate from DEFINE FIELD defaults."""
    c = await _fresh_client()
    try:
        await c.execute(
            "CREATE compliance_check SET intent_id = 'intent:a', "
            "region_id = 'code_region:x', content_hash = 'h', "
            "compliant = true, confidence = 'high'"
            # phase, explanation, commit_hash, checked_at omitted
        )
        rows = await c.query(
            "SELECT phase, explanation, commit_hash, checked_at "
            "FROM compliance_check WHERE intent_id = 'intent:a'"
        )
        assert len(rows) == 1
        row = rows[0]
        assert row["phase"] == "drift"
        assert row["explanation"] == ""
        assert row["commit_hash"] == ""
        assert row["checked_at"], "checked_at should auto-populate via time::now()"
    finally:
        await c.close()


# ── Secondary indexes exist ──────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_secondary_indexes_support_lookup_queries():
    """Queries on intent_id, region_id, commit_hash should return results.

    We don't introspect INFO FOR TABLE (empty in embedded mode per CLAUDE.md);
    we just verify the query planner can use the index by exercising it.
    """
    c = await _fresh_client()
    try:
        for i in range(3):
            await c.execute(
                "CREATE compliance_check SET intent_id = $i, region_id = $r, "
                "content_hash = $h, commit_hash = $cm, compliant = true, "
                "confidence = 'high', explanation = '', phase = 'drift'",
                {
                    "i": f"intent:{i}",
                    "r": f"code_region:{i}",
                    "h": f"hash_{i}",
                    "cm": "commit_xyz" if i == 1 else f"commit_{i}",
                },
            )
        rows = await c.query(
            "SELECT intent_id FROM compliance_check WHERE commit_hash = 'commit_xyz'"
        )
        assert len(rows) == 1
        assert rows[0]["intent_id"] == "intent:1"
    finally:
        await c.close()


# ── Migration idempotency ────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_migrate_is_idempotent_at_v3():
    """Calling migrate() twice is a no-op (version already at target)."""
    c = await _fresh_client()
    try:
        # Already at v3 from _fresh_client(); running migrate() again is fine.
        await migrate(c)
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows[0]["version"] == 3
    finally:
        await c.close()
