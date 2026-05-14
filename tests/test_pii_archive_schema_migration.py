"""#221 Phase A — schema migration regression tests.

Verify the additive ``input_span.archive_key`` field migration (v19→v20)
is non-destructive and doesn't break the existing ledger surface.

Sociable per CLAUDE.md: real ``LedgerClient`` over ``memory://``,
real ``init_schema`` + ``migrate`` flow.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import SCHEMA_VERSION, init_schema, migrate


@pytest.mark.asyncio
async def test_schema_version_is_at_least_20_after_migration() -> None:
    """v19→v20 lifts SCHEMA_VERSION; init_schema + migrate must reach it."""
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] >= 20
        assert SCHEMA_VERSION >= 20
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_input_span_accepts_archive_key_field_after_migration() -> None:
    """Confirm the additive field is queryable after migration.

    Insert an input_span row with archive_key set; read it back; assert
    the value round-trips. Verifies the field exists and is writeable.
    """
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        await client.execute(
            """
            CREATE input_span SET
                text = 'sample verbatim',
                source_type = 'manual',
                source_ref = 'test-ref',
                speakers = ['someone@example.com'],
                meeting_date = '2026-05-14',
                archive_key = 'abcdef0123456789'
            """
        )
        rows = await client.query(
            "SELECT archive_key, text FROM input_span WHERE source_ref = 'test-ref'"
        )
        assert rows
        assert rows[0]["archive_key"] == "abcdef0123456789"
        assert rows[0]["text"] == "sample verbatim"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_legacy_input_span_rows_get_empty_archive_key_default() -> None:
    """Legacy ingest path (no archive_key in CREATE) → defaults to ''.

    This is the load-bearing Phase A contract: rows ingested via the
    current (Phase A) code path will have archive_key='' and the
    read-path falls back to input_span.text. Phase B's ingest cutover
    flips this default by injecting archive_key at write time.
    """
    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        # Insert WITHOUT archive_key — the DEFAULT '' must kick in.
        await client.execute(
            """
            CREATE input_span SET
                text = 'legacy span without archive key',
                source_type = 'manual',
                source_ref = 'legacy-ref',
                speakers = []
            """
        )
        rows = await client.query(
            "SELECT archive_key FROM input_span WHERE source_ref = 'legacy-ref'"
        )
        assert rows
        # DEFAULT '' must be applied
        assert rows[0]["archive_key"] == ""

    finally:
        await client.close()


@pytest.mark.asyncio
async def test_existing_input_span_assert_text_not_relaxed() -> None:
    """Phase A does NOT relax the input_span.text ASSERT.

    Pin the non-relaxation: inserting a row with empty text must still
    fail. Phase B is the cycle that introduces ASSERT
    ``archive_key != '' OR text != ''`` and relaxes the current
    ``string::len > 0`` ASSERT on text. This test confirms Phase A
    has NOT moved that goalpost.
    """
    from ledger.client import LedgerError

    client = LedgerClient(url="memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        with pytest.raises((LedgerError, Exception)):  # SurrealDB will reject
            await client.execute(
                """
                CREATE input_span SET
                    text = '',
                    source_type = 'manual',
                    source_ref = 'reject-me',
                    speakers = []
                """
            )
    finally:
        await client.close()
