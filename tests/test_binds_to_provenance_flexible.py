"""Tests for #72: binds_to.provenance FLEXIBLE + v11→v12 migration.

The pre-fix schema declared ``DEFINE FIELD provenance ON binds_to TYPE
object DEFAULT {}`` (no FLEXIBLE), which caused SurrealDB v2 to silently
drop any unrecognized sub-field on write. Every existing row has
``provenance = {}``. The fix: add FLEXIBLE to the canonical schema; the
v11→v12 migration redefines the existing column via OVERWRITE and stamps
pre-fix rows with ``{"_pre_schema_v12": true}`` so consumers can tell
"lost provenance" apart from "genuinely empty".
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import (
    SCHEMA_VERSION,
    _migrate_v11_to_v12,
    _MIGRATIONS,
    _get_schema_version,
    _set_schema_version,
    init_schema,
    migrate,
)

async def _fresh_client() -> LedgerClient:
    client = LedgerClient(url="memory://", ns="bicameral", db="ledger")
    await client.connect()
    return client


# ── Schema constants (sync) ─────────────────────────────────────────────────


def test_schema_version_bumped() -> None:
    assert SCHEMA_VERSION == 12


def test_v12_migration_registered() -> None:
    assert _MIGRATIONS.get(12) is _migrate_v11_to_v12


# ── Fresh DB at v12 ─────────────────────────────────────────────────────────


async def test_provenance_object_round_trips_at_v12() -> None:
    """At v12, RELATE-ing a binds_to with structured provenance preserves all keys."""
    client = await _fresh_client()
    try:
        await init_schema(client)
        await migrate(client)

        # Set up minimum data: input_span, decision, code_region, then RELATE.
        await client.execute(
            "CREATE input_span:s1 SET text = 'verbatim', source_type = 'transcript'"
        )
        await client.execute(
            "CREATE decision:d1 SET description = 'd', source_type = 'transcript', canonical_id = 'cid-d1'"
        )
        await client.execute(
            "CREATE code_region:r1 SET file_path = 'a.py', symbol_name = 'fn', "
            "start_line = 1, end_line = 5"
        )
        await client.execute(
            "RELATE decision:d1->binds_to->code_region:r1 SET "
            'confidence = 0.9, provenance = {"source": "llm", "model": "haiku-4-5", "score": 0.87}'
        )

        rows = await client.query("SELECT provenance FROM binds_to")
        assert len(rows) == 1
        prov = rows[0]["provenance"]
        assert prov.get("source") == "llm"
        assert prov.get("model") == "haiku-4-5"
        assert prov.get("score") == 0.87
    finally:
        await client.close()


# ── v11 → v12 migration ─────────────────────────────────────────────────────


async def _seed_v11_with_empty_provenance(client: LedgerClient, n: int) -> None:
    """Initialize schema, then force version back to 11 and add rows with `{}`."""
    await init_schema(client)
    await migrate(client)
    await _set_schema_version(client, 11)
    await client.execute(
        "CREATE input_span:s1 SET text = 'x', source_type = 'transcript'"
    )
    for i in range(n):
        await client.execute(
            f"CREATE decision:d{i} SET description = 'd', "
            f"source_type = 'transcript', canonical_id = 'cid-{i}'"
        )
        await client.execute(
            f"CREATE code_region:r{i} SET file_path = 'a.py', symbol_name = 'fn', "
            f"start_line = {i}, end_line = {i + 1}"
        )
        await client.execute(
            f"RELATE decision:d{i}->binds_to->code_region:r{i} SET "
            "confidence = 0.5, provenance = {}"
        )


async def test_v12_migration_stamps_legacy_rows() -> None:
    """Seed at v11 with 3 binds_to having `{}`; migrate; assert all 3 stamped."""
    client = await _fresh_client()
    try:
        await _seed_v11_with_empty_provenance(client, n=3)

        before = await client.query("SELECT provenance FROM binds_to")
        assert len(before) == 3
        assert all(r["provenance"] == {} for r in before)

        await migrate(client)

        after = await client.query("SELECT provenance FROM binds_to")
        assert len(after) == 3
        assert all(r["provenance"] == {"_pre_schema_v12": True} for r in after)

        version = await _get_schema_version(client)
        assert version == 12
    finally:
        await client.close()


async def test_v12_migration_idempotent() -> None:
    """Already-at-v12 db: migrate() is a no-op (orchestrator returns early)."""
    client = await _fresh_client()
    try:
        await init_schema(client)
        await migrate(client)
        # Add a row with structured provenance.
        await client.execute(
            "CREATE input_span:s1 SET text = 'x', source_type = 'transcript'"
        )
        await client.execute(
            "CREATE decision:d1 SET description = 'd', source_type = 'transcript', canonical_id = 'cid-d1'"
        )
        await client.execute(
            "CREATE code_region:r1 SET file_path = 'a.py', symbol_name = 'fn', "
            "start_line = 1, end_line = 5"
        )
        await client.execute(
            "RELATE decision:d1->binds_to->code_region:r1 SET "
            'confidence = 0.9, provenance = {"source": "llm"}'
        )

        await migrate(client)  # second call

        rows = await client.query("SELECT provenance FROM binds_to")
        assert rows[0]["provenance"] == {"source": "llm"}  # unchanged
    finally:
        await client.close()


async def test_v12_migration_row_count_accurate() -> None:
    """Mixed seed: 5 empty + 2 structured at v11. After migrate: 5 stamped, 2 untouched."""
    client = await _fresh_client()
    try:
        await init_schema(client)
        await migrate(client)
        await _set_schema_version(client, 11)
        await client.execute(
            "CREATE input_span:s1 SET text = 'x', source_type = 'transcript'"
        )
        # 5 with {} provenance
        for i in range(5):
            await client.execute(
                f"CREATE decision:e{i} SET description = 'd', source_type = 'transcript', canonical_id = 'cid-e{i}'"
            )
            await client.execute(
                f"CREATE code_region:re{i} SET file_path = 'a.py', symbol_name = 'fn', "
                f"start_line = {i}, end_line = {i + 1}"
            )
            await client.execute(
                f"RELATE decision:e{i}->binds_to->code_region:re{i} SET "
                "confidence = 0.5, provenance = {}"
            )

        await migrate(client)

        stamped = await client.query(
            "SELECT count() AS n FROM binds_to WHERE provenance._pre_schema_v12 = true GROUP ALL"
        )
        assert stamped[0]["n"] == 5
    finally:
        await client.close()


async def test_legacy_stamp_is_filterable() -> None:
    """After migration, the stamp is queryable as a normal field."""
    client = await _fresh_client()
    try:
        await _seed_v11_with_empty_provenance(client, n=2)
        await migrate(client)

        rows = await client.query(
            "SELECT decision_id FROM binds_to WHERE provenance._pre_schema_v12 = true"
        )
        assert len(rows) == 2
    finally:
        await client.close()
