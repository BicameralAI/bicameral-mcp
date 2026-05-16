"""Sociable regression tests for ``upsert_input_span`` (queries.py).

Two related defects motivated this suite:

1. **Schema bug (v23 and earlier)**: ``idx_input_span_dedup`` was UNIQUE on
   ``(source_type, source_ref, text)``. Phase B-1 archive-keyed rows write
   ``text=''``, so two distinct ``archive_key`` values sharing
   ``(source_type, source_ref)`` collided on the empty-text slot. Surfaced
   as a 500 from ``/history`` once any second archive-keyed write to the
   same bucket landed. Fixed in v24 by adding ``archive_key`` as a 4th
   index field.

2. **Race in ``upsert_input_span``**: the SELECT-then-CREATE pattern lost
   to a concurrent writer for the same ``archive_key`` — both passed the
   SELECT, both attempted CREATE, the loser crashed with a unique-index
   violation. Fixed by wrapping CREATE in try/except and re-SELECTing on
   ``"already contains"`` (the v2 substring pinned by
   ``test_schema_recoverable_errors.py``).

Sociable testing per ``pilot/mcp/CLAUDE.md``: a real ``LedgerClient`` over
``memory://``, real ``init_schema`` + ``migrate`` against the production
schema definitions. No mocks.
"""

from __future__ import annotations

import asyncio

import pytest

from ledger.client import LedgerClient
from ledger.queries import upsert_input_span
from ledger.schema import init_schema, migrate


async def _fresh_client(suffix: str) -> LedgerClient:
    c = LedgerClient(url="memory://", ns="bicameral_test", db=f"safe_upsert_{suffix}")
    await c.connect()
    await init_schema(c)
    await migrate(c)
    return c


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_two_archive_keys_same_source_bucket_coexist() -> None:
    """Schema v24 fix: two archive-keyed spans in the same
    ``(source_type, source_ref)`` bucket must be distinguishable on
    ``archive_key`` and both succeed."""
    c = await _fresh_client("coexist")
    try:
        id_a = await upsert_input_span(
            c,
            text="",
            source_type="transcript",
            source_ref="meeting-2026-05-15",
            archive_key="a" * 64,
        )
        id_b = await upsert_input_span(
            c,
            text="",
            source_type="transcript",
            source_ref="meeting-2026-05-15",
            archive_key="b" * 64,
        )
        assert id_a, "first archive-keyed span should land"
        assert id_b, "second archive-keyed span should land (post-v24)"
        assert id_a != id_b, "two distinct archive_keys must produce distinct rows"

        rows = await c.query(
            "SELECT type::string(id) AS id, archive_key FROM input_span "
            "WHERE source_ref = 'meeting-2026-05-15'"
        )
        keys = sorted(r.get("archive_key", "") for r in rows)
        assert keys == ["a" * 64, "b" * 64]
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_same_archive_key_is_idempotent() -> None:
    """Pre-existing dedup contract: calling with the same ``archive_key``
    twice returns the same row id (no second row created)."""
    c = await _fresh_client("dedup")
    try:
        id_1 = await upsert_input_span(
            c, text="", source_type="transcript", source_ref="x", archive_key="k" * 64
        )
        id_2 = await upsert_input_span(
            c, text="", source_type="transcript", source_ref="x", archive_key="k" * 64
        )
        assert id_1 and id_1 == id_2

        count = await c.query("SELECT count() FROM input_span GROUP ALL")
        assert count == [{"count": 1}]
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_concurrent_same_archive_key_race() -> None:
    """Safe-upsert race fix: two concurrent ingests of the same
    ``archive_key`` must return the same row id — the loser's CREATE
    catches ``"already contains"`` and re-SELECTs instead of crashing."""
    c = await _fresh_client("race")
    try:
        results = await asyncio.gather(
            upsert_input_span(
                c, text="", source_type="transcript", source_ref="r", archive_key="z" * 64
            ),
            upsert_input_span(
                c, text="", source_type="transcript", source_ref="r", archive_key="z" * 64
            ),
            upsert_input_span(
                c, text="", source_type="transcript", source_ref="r", archive_key="z" * 64
            ),
            return_exceptions=True,
        )
        for r in results:
            assert not isinstance(r, BaseException), f"concurrent ingest raised: {r!r}"
        assert len(set(results)) == 1, f"concurrent ingests returned different ids: {results}"

        count = await c.query("SELECT count() FROM input_span GROUP ALL")
        assert count == [{"count": 1}]
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_legacy_text_path_safe_upsert_under_race() -> None:
    """The legacy (text-only, no archive_key) path is also wrapped in
    safe-upsert. Concurrent writers with the same
    ``(source_type, source_ref, text)`` must converge on one row."""
    c = await _fresh_client("legacy")
    try:
        text = "BM25 must return ranked results with provenance"
        results = await asyncio.gather(
            upsert_input_span(c, text=text, source_type="transcript", source_ref="m1"),
            upsert_input_span(c, text=text, source_type="transcript", source_ref="m1"),
            return_exceptions=True,
        )
        for r in results:
            assert not isinstance(r, BaseException), f"legacy concurrent raised: {r!r}"
        assert len(set(results)) == 1
        count = await c.query("SELECT count() FROM input_span GROUP ALL")
        assert count == [{"count": 1}]
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_mvcc_conflict_substring_pinned() -> None:
    """``upsert_input_span`` retries on the v2 MVCC conflict string
    ``"failed to commit transaction"`` (with hint "can be retried").
    If a future surrealdb-py bump changes this format, the safe-upsert
    retry silently stops working and concurrent ingests start crashing
    again. This test provokes the conflict directly and asserts the
    expected substring still appears so a maintainer can update
    ``_MVCC_RETRY_SUBSTRING`` in ``ledger/queries.py``."""
    from ledger.client import LedgerError
    from ledger.queries import _MVCC_RETRY_SUBSTRING

    c = await _fresh_client("mvcc_pin")
    try:
        # Force a concurrent CREATE on a UNIQUE-indexed column. The first
        # write wins; the second loses to MVCC inside the embedded engine.
        await c.execute("DEFINE TABLE mvcc_probe SCHEMAFULL")
        await c.execute("DEFINE FIELD k ON mvcc_probe TYPE string")
        await c.execute("DEFINE INDEX idx_mvcc_probe_k ON mvcc_probe FIELDS k UNIQUE")

        async def _create() -> None:
            await c.query("CREATE mvcc_probe SET k = $k", {"k": "race"})

        results = await asyncio.gather(_create(), _create(), _create(), return_exceptions=True)
        errors = [r for r in results if isinstance(r, LedgerError)]
        assert errors, "expected at least one concurrent writer to lose"
        # At least one error must carry the pinned substring; otherwise
        # the retry catch is no longer load-bearing.
        msgs = [str(e).lower() for e in errors]
        assert any(_MVCC_RETRY_SUBSTRING in m for m in msgs), (
            "SurrealDB MVCC error string changed — the upsert_input_span "
            "retry catch will no longer cover it. Update _MVCC_RETRY_SUBSTRING "
            f"in ledger/queries.py to match one of: {msgs}"
        )
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v24_migration_is_idempotent() -> None:
    """``_migrate_v23_to_v24`` must be safe to re-run — the migration
    uses ``DEFINE INDEX OVERWRITE`` via ``_execute_define_idempotent``,
    so a second invocation is a no-op."""
    from ledger.schema import _migrate_v23_to_v24

    c = await _fresh_client("idempotent")
    try:
        # First run already happened inside _fresh_client → migrate().
        # A second invocation must not raise.
        await _migrate_v23_to_v24(c)
        await _migrate_v23_to_v24(c)

        # And the index must still distinguish on archive_key.
        id_a = await upsert_input_span(
            c, text="", source_type="transcript", source_ref="b", archive_key="1" * 64
        )
        id_b = await upsert_input_span(
            c, text="", source_type="transcript", source_ref="b", archive_key="2" * 64
        )
        assert id_a and id_b and id_a != id_b
    finally:
        await c.close()
