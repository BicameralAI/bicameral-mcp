"""Persistent-db schema smoke tests.

Runs init_schema and migrate against a real file-backed surrealkv:// database
(not memory://) to catch idempotency regressions, migration chain failures,
and schema compatibility errors before they ship.

These tests must NOT be run with SURREAL_URL=memory:// — each test creates
its own isolated surrealkv path under pytest's tmp_path fixture.
"""

from __future__ import annotations

import pytest

from ledger.adapter import _surrealkv_url_for_path
from ledger.client import LedgerClient
from ledger.schema import (
    SCHEMA_VERSION,
    DestructiveMigrationRequired,
    SchemaVersionTooNew,
    init_schema,
    migrate,
)

pytestmark = [pytest.mark.phase2]


@pytest.fixture
async def file_client(tmp_path):
    """Fresh file-backed LedgerClient for each test."""
    url = _surrealkv_url_for_path(tmp_path / "ledger.db")
    client = LedgerClient(url=url, ns="bicameral", db="ledger")
    await client.connect()
    try:
        yield client
    finally:
        try:
            await client.close()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_init_schema_twice_no_crash(file_client):
    """Running init_schema on an already-initialized db must not raise.

    This is the class of bug that caused the v0.4.22 regression and the
    v0.6.5 SurrealError hotfix. A second init_schema must be a no-op.
    """
    await init_schema(file_client)  # first call (already done in fixture connect)
    await init_schema(file_client)  # second call — must not raise


@pytest.mark.asyncio
async def test_migration_chain_from_scratch(tmp_path):
    """Full migration chain from v0 baseline must reach SCHEMA_VERSION.

    Simulates a brand-new user who installs the latest binary against an
    empty database.
    """
    url = _surrealkv_url_for_path(tmp_path / "ledger.db")
    client = LedgerClient(url=url, ns="bicameral", db="ledger")
    await client.connect()
    try:
        await init_schema(client)
        # schema_meta is empty after init_schema — version is 0
        await migrate(client, allow_destructive=True)
        rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows and rows[0]["version"] == SCHEMA_VERSION
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_destructive_migration_blocked(tmp_path):
    """DESTRUCTIVE_MIGRATIONS is currently empty — migrate() from any version
    succeeds without raising DestructiveMigrationRequired.

    If a future migration is added to DESTRUCTIVE_MIGRATIONS, update this
    test to verify the block-then-allow pattern. For now, verify that
    allow_destructive=False is safe when there are no destructive steps.
    """
    from ledger.schema import DESTRUCTIVE_MIGRATIONS
    url = _surrealkv_url_for_path(tmp_path / "ledger.db")
    client = LedgerClient(url=url, ns="bicameral", db="ledger")
    await client.connect()
    try:
        await init_schema(client)
        # Inject a v3 schema version to simulate a pre-current database
        await client.execute("DELETE FROM schema_meta")
        await client.execute(
            "CREATE schema_meta SET version = $v, migrated_at = time::now()",
            {"v": 3},
        )
        if DESTRUCTIVE_MIGRATIONS:
            with pytest.raises(DestructiveMigrationRequired):
                await migrate(client, allow_destructive=False)
        else:
            # No destructive migrations — allow_destructive=False succeeds
            await migrate(client, allow_destructive=False)
            rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
            assert rows and rows[0]["version"] == SCHEMA_VERSION
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_destructive_migration_allowed(tmp_path):
    """allow_destructive=True lets the chain complete past a destructive step."""
    url = _surrealkv_url_for_path(tmp_path / "ledger.db")
    client = LedgerClient(url=url, ns="bicameral", db="ledger")
    await client.connect()
    try:
        await init_schema(client)
        await client.execute("DELETE FROM schema_meta")
        await client.execute(
            "CREATE schema_meta SET version = $v, migrated_at = time::now()",
            {"v": 3},
        )
        await migrate(client, allow_destructive=True)
        rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows and rows[0]["version"] == SCHEMA_VERSION
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_schema_version_too_new_raises(tmp_path):
    """DB schema newer than code raises SchemaVersionTooNew with upgrade hint."""
    url = _surrealkv_url_for_path(tmp_path / "ledger.db")
    client = LedgerClient(url=url, ns="bicameral", db="ledger")
    await client.connect()
    try:
        await init_schema(client)
        future_version = SCHEMA_VERSION + 1
        await client.execute("DELETE FROM schema_meta")
        await client.execute(
            "CREATE schema_meta SET version = $v, migrated_at = time::now()",
            {"v": future_version},
        )
        with pytest.raises(SchemaVersionTooNew) as exc_info:
            await migrate(client)
        msg = str(exc_info.value)
        assert "pipx upgrade bicameral-mcp" in msg
    finally:
        await client.close()
