"""Schema tests for compliance_check (v4) — LLM verification cache.

Validates Phase 1 of the unified compliance verification plan:
thoughts/shared/plans/2026-04-20-ingest-time-verification.md.

The compliance_check table is the cache layer that lets reads project
REFLECTED / DRIFTED / PENDING without calling an LLM. Cache key is
``(decision_id, region_id, content_hash)`` — the hash of the code shape
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
    await migrate(c, allow_destructive=True)
    return c


# ── Version stamp ────────────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_schema_version_is_current_after_migrate():
    """Migrations bring schema_meta to the current SCHEMA_VERSION."""
    c = await _fresh_client()
    try:
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows, "schema_meta row missing after migrate"
        assert rows[0]["version"] == SCHEMA_VERSION
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
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h, verdict = 'compliant', confidence = 'high', "
            "explanation = 'first', phase = 'ingest'",
            {"i": "intent:a", "r": "code_region:x", "h": "hash_abc"},
        )

        with pytest.raises(LedgerError, match="idx_cc_cache_key"):
            await c.execute(
                "CREATE compliance_check SET decision_id = $i, region_id = $r, "
                "content_hash = $h, verdict = 'drifted', confidence = 'low', "
                "explanation = 'second', phase = 'drift'",
                {"i": "intent:a", "r": "code_region:x", "h": "hash_abc"},
            )

        # Defensive: the first row's verdict='compliant' must still be stored.
        rows = await c.query("SELECT verdict FROM compliance_check")
        assert len(rows) == 1
        assert rows[0]["verdict"] == "compliant"
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
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h1, verdict = 'compliant', confidence = 'high', "
            "explanation = 'ok at h1', phase = 'ingest'",
            {"i": "intent:a", "r": "code_region:x", "h1": "hash_aaa"},
        )
        await c.execute(
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h2, verdict = 'drifted', confidence = 'medium', "
            "explanation = 'broke at h2', phase = 'drift'",
            {"i": "intent:a", "r": "code_region:x", "h2": "hash_bbb"},
        )
        rows = await c.query(
            "SELECT content_hash FROM compliance_check "
            "WHERE decision_id = 'intent:a' AND region_id = 'code_region:x' "
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
                "CREATE compliance_check SET decision_id = 'intent:a', "
                "region_id = 'code_region:x', content_hash = 'h', "
                "verdict = 'compliant', confidence = 'very_high', "
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
                "CREATE compliance_check SET decision_id = 'intent:a', "
                "region_id = 'code_region:x', content_hash = 'h', "
                "verdict = 'compliant', confidence = 'high', "
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
        for i, phase in enumerate(("ingest", "drift", "regrounding", "supersession", "divergence")):
            await c.execute(
                "CREATE compliance_check SET decision_id = $i, region_id = $r, "
                "content_hash = $h, verdict = 'compliant', confidence = 'high', "
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
            "CREATE compliance_check SET decision_id = 'intent:a', "
            "region_id = 'code_region:x', content_hash = 'h', "
            "verdict = 'compliant', confidence = 'high'"
            # phase, explanation, commit_hash, checked_at omitted
        )
        rows = await c.query(
            "SELECT phase, explanation, commit_hash, checked_at "
            "FROM compliance_check WHERE decision_id = 'intent:a'"
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
                "CREATE compliance_check SET decision_id = $i, region_id = $r, "
                "content_hash = $h, commit_hash = $cm, verdict = 'compliant', "
                "confidence = 'high', explanation = '', phase = 'drift'",
                {
                    "i": f"intent:{i}",
                    "r": f"code_region:{i}",
                    "h": f"hash_{i}",
                    "cm": "commit_xyz" if i == 1 else f"commit_{i}",
                },
            )
        rows = await c.query(
            "SELECT decision_id FROM compliance_check WHERE commit_hash = 'commit_xyz'"
        )
        assert len(rows) == 1
        assert rows[0]["decision_id"] == "intent:1"
    finally:
        await c.close()


# ── Migration idempotency ────────────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_migrate_is_idempotent():
    """Calling migrate() twice is a no-op (version already at target)."""
    c = await _fresh_client()
    try:
        # Already at current version from _fresh_client(); running migrate() again is fine.
        await migrate(c, allow_destructive=True)
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows[0]["version"] == SCHEMA_VERSION
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_init_schema_is_idempotent_against_existing_db():
    """Regression for the v0.4.20→v0.4.22 hotfix: init_schema must
    survive re-running against a database that already has every
    analyzer, table, field, and index defined.

    SurrealDB v2 rejects redundant DEFINE statements with "already
    exists". Pre-v0.4.20 that rejection was silently discarded by the
    client — Phase 1b made the client raise, which turned every MCP
    server startup against a persistent surrealkv DB into an
    unrecoverable error because init_schema runs on every connect.

    Post-hotfix: init_schema tolerates "already exists" so re-connects
    are safe, while still surfacing real DDL errors.
    """
    c = LedgerClient(url="memory://", ns="init_idem_test", db="ledger")
    try:
        await c.connect()
        # First init — creates everything.
        await init_schema(c)
        # Second init — must not raise despite every DEFINE being redundant.
        await init_schema(c)
        # Third init, just to lock the invariant.
        await init_schema(c)

        # Sanity: schema still works after repeated inits.
        await c.execute("CREATE intent SET description = 'init-idem test', source_type = 'manual'")
        rows = await c.query("SELECT description FROM intent")
        assert len(rows) == 1
        assert rows[0]["description"] == "init-idem test"
    finally:
        await c.close()
