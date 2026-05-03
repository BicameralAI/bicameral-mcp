"""Functionality tests for team_server Phase 0 — schema migration v1->v2."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")


@pytest.mark.asyncio
async def test_v1_to_v2_migration_drops_old_index_and_defines_new():
    """Behaviorally verify the post-v2 index shape: a duplicate
    (source_type, source_ref) raises uniqueness violation, while
    differing content_hash on the same key is what previously got
    created — now it conflicts.
    """
    from ledger.client import LedgerError
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        # Seed two rows that would have been distinct under v1 (same
        # source_type+source_ref, different content_hash). The v2 index
        # must reject the second.
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: 'slack', source_ref: 'X/1', "
            "content_hash: 'h1', canonical_extraction: {}, model_version: 'm' }"
        )
        with pytest.raises(LedgerError):
            await client.query(
                "CREATE extraction_cache CONTENT { source_type: 'slack', source_ref: 'X/1', "
                "content_hash: 'h2', canonical_extraction: {}, model_version: 'm' }"
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_v1_to_v2_migration_is_idempotent():
    """Behavior: second invocation of ensure_schema is safe and
    leaves the v2 uniqueness invariant intact."""
    from ledger.client import LedgerError
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await ensure_schema(client)
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: 'slack', source_ref: 'X/2', "
            "content_hash: 'h1', canonical_extraction: {}, model_version: 'm' }"
        )
        with pytest.raises(LedgerError):
            await client.query(
                "CREATE extraction_cache CONTENT { source_type: 'slack', source_ref: 'X/2', "
                "content_hash: 'h2', canonical_extraction: {}, model_version: 'm' }"
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_schema_version_row_records_current_version_after_migrations_apply():
    """Behavior: schema_version table holds exactly one row whose
    `version` field equals SCHEMA_VERSION; UPSERT-semantics keep the
    row count at 1 across multiple ensure_schema calls."""
    from team_server.db import build_client
    from team_server.schema import SCHEMA_VERSION, ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        rows = await client.query("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0]["version"] == SCHEMA_VERSION

        await ensure_schema(client)
        rows = await client.query("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0]["version"] == SCHEMA_VERSION
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ensure_schema_dispatches_callable_migrations(monkeypatch):
    """Behavior: ensure_schema awaits each entry in _MIGRATIONS as a
    callable, passing the LedgerClient as its sole argument."""
    from team_server import schema as schema_mod
    from team_server.db import build_client

    calls = []

    async def stub_migration(client):
        calls.append(client)

    monkeypatch.setattr(schema_mod, "_MIGRATIONS", {99: stub_migration})

    client = build_client()
    await client.connect()
    try:
        await schema_mod.ensure_schema(client)
        assert len(calls) == 1
        assert calls[0] is client
    finally:
        await client.close()
