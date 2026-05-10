"""Functional tests for the v15→v16 migration + bicameral_meta init (#252 Layer 2)."""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import (
    _MIGRATIONS,
    SCHEMA_VERSION,
    _migrate_v15_to_v16,
    init_schema,
    migrate,
)


@pytest.fixture
async def fresh_client():
    client = LedgerClient("memory://")
    await client.connect()
    yield client
    await client.close()


async def test_migrate_v15_to_v16_is_no_op_for_existing_v15_ledger(fresh_client):
    await init_schema(fresh_client)
    # Force the recorded version to v15 (simulate an existing v15 ledger).
    await fresh_client.execute("DELETE FROM schema_meta")
    await fresh_client.execute(
        "CREATE schema_meta SET version = $v, migrated_at = time::now()", {"v": 15}
    )
    await migrate(fresh_client, allow_destructive=True)
    rows = await fresh_client.query("SELECT version FROM schema_meta LIMIT 1")
    assert rows[0]["version"] == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 16  # current floor — bumps land here as version increments
    bm_rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    # Migration body is a no-op; sentinel writes happen in adapter.connect, not here.
    assert bm_rows == []


def test_migration_registry_includes_v15_to_v16():
    assert _MIGRATIONS[16] is _migrate_v15_to_v16


async def test_init_schema_creates_bicameral_meta_table(fresh_client):
    await init_schema(fresh_client)
    # Existence proof: SELECT against the table returns [] without error
    # (per pilot/mcp/CLAUDE.md note that INFO FOR TABLE returns empty in
    # embedded SurrealDB v2 mode, the empty-SELECT-without-error pattern
    # is the canonical existence check).
    rows = await fresh_client.query("SELECT * FROM bicameral_meta LIMIT 1")
    assert rows == []
