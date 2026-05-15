"""#221 Phase B-1 — schema migration v21→v22 regression tests.

Verifies the deterministic-gate ASSERT on ``input_span.text``:
- New row with ``text=''`` AND ``archive_key=''`` → REJECTED
- New row with ``text='legacy'`` AND ``archive_key=''`` → accepted
- New row with ``text=''`` AND ``archive_key='abc'`` → accepted
- Legacy v21 rows continue to satisfy the new ASSERT

Sociable per CLAUDE.md: real LedgerClient over memory://, real
init_schema + migrate flow.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient, LedgerError
from ledger.schema import SCHEMA_VERSION, init_schema, migrate


@pytest.mark.asyncio
async def test_schema_version_advances_to_22_after_migration() -> None:
    """v21→v22 lifts SCHEMA_VERSION; init_schema + migrate must reach it."""
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] >= 22
        assert SCHEMA_VERSION >= 22
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ingest_with_both_text_and_archive_key_empty_is_rejected_by_assert() -> None:
    """Load-bearing deterministic gate (#205 doctrine, gate_kind: schema).

    A row with BOTH ``text=''`` AND ``archive_key=''`` MUST be rejected
    by SurrealDB itself — not by handler-side code. This proves the
    gate is schema-level, refactor-resistant."""
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        with pytest.raises((LedgerError, Exception)):
            await client.execute(
                """
                CREATE input_span SET
                    text = '',
                    source_type = 'manual',
                    source_ref = 'test-ref-empty-both',
                    speakers = [],
                    archive_key = ''
                """
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ingest_with_only_text_non_empty_succeeds() -> None:
    """Legacy-shape path: text!='' and archive_key=''."""
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        await client.execute(
            """
            CREATE input_span SET
                text = 'legacy verbatim content',
                source_type = 'manual',
                source_ref = 'legacy-ref',
                speakers = []
            """
        )
        rows = await client.query(
            "SELECT text, archive_key FROM input_span WHERE source_ref = 'legacy-ref'"
        )
        assert rows
        assert rows[0]["text"] == "legacy verbatim content"
        assert rows[0]["archive_key"] == ""
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ingest_with_only_archive_key_non_empty_succeeds() -> None:
    """New Phase-B-1 cutover path: text='' and archive_key!=''."""
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        await client.execute(
            """
            CREATE input_span SET
                text = '',
                source_type = 'manual',
                source_ref = 'new-ref',
                speakers = [],
                archive_key = 'sha256:deadbeef'
            """
        )
        rows = await client.query(
            "SELECT text, archive_key FROM input_span WHERE source_ref = 'new-ref'"
        )
        assert rows
        assert rows[0]["text"] == ""
        assert rows[0]["archive_key"] == "sha256:deadbeef"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ingest_with_both_text_and_archive_key_non_empty_is_allowed() -> None:
    """ASSERT is "at least one is non-empty"; both is also fine.

    Phase B-1 documents that handler-side code MUST set text='' for
    new rows, but the schema permits a transitional shape where both
    are populated (e.g., a migration that backfills archive_key while
    leaving text populated for safety)."""
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        await client.execute(
            """
            CREATE input_span SET
                text = 'transitional content',
                source_type = 'manual',
                source_ref = 'transition-ref',
                speakers = [],
                archive_key = 'sha256:transition'
            """
        )
        rows = await client.query(
            "SELECT text, archive_key FROM input_span WHERE source_ref = 'transition-ref'"
        )
        assert rows
        assert rows[0]["text"] == "transitional content"
        assert rows[0]["archive_key"] == "sha256:transition"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_legacy_v20_row_continues_to_satisfy_v22_assert() -> None:
    """Backward-compat: rows created under v20's schema (text required
    string::len > 0; no archive_key) still satisfy v22's ASSERT.

    Test simulates by inserting a row in the legacy shape after the
    migration runs — text non-empty, archive_key empty (the v20 default).
    The new ASSERT permits this via the text clause.
    """
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        # Insert in the legacy shape — text only, no archive_key
        await client.execute(
            """
            CREATE input_span SET
                text = 'pre-migration content',
                source_type = 'manual',
                source_ref = 'pre-migration-ref',
                speakers = ['alice@example.com']
            """
        )
        rows = await client.query(
            "SELECT text, archive_key FROM input_span WHERE source_ref = 'pre-migration-ref'"
        )
        assert rows
        assert rows[0]["text"] == "pre-migration content"
        assert rows[0]["archive_key"] == ""  # default
    finally:
        await client.close()
