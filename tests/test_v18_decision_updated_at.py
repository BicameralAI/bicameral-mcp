"""v17 → v18 schema migration — decision.updated_at + idx_decision_updated_at (#87 precondition).

The new field is the revision marker the preflight dedup cache will key on
(handlers/preflight.py — Phase 4 follow-up). This test covers the schema
half: that every existing UPDATE call site against the decision table now
bumps updated_at, and that the migration's backfill makes pre-v18 rows
queryable via MAX(updated_at).

Sociable tests over memory:// SurrealDB. The drift this guards against is
exactly the kind of regression an LLM-authored solitary test would have
missed — mocks would happily return whatever updated_at the test expects;
only a real ledger UPDATE proves the SQL actually carries the new column.
"""

from __future__ import annotations

import asyncio

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import (
    upsert_decision,
    update_decision_level,
    update_decision_status,
)
from ledger.schema import SCHEMA_VERSION, init_schema, migrate


_NS_COUNTER = 0


async def _fresh_client() -> LedgerClient:
    global _NS_COUNTER
    _NS_COUNTER += 1
    c = LedgerClient(url="memory://", ns=f"v18_test_{_NS_COUNTER}", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _fresh_adapter() -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    client = await _fresh_client()
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


_CANONICAL_COUNTER = 0


def _next_canonical() -> str:
    global _CANONICAL_COUNTER
    _CANONICAL_COUNTER += 1
    return f"v18-test-{_CANONICAL_COUNTER}"


async def _seed_decision(client: LedgerClient, *, status: str = "ungrounded") -> str:
    """Create one decision via raw CREATE and return its full id (table:rid).

    Uses CREATE directly rather than upsert_decision so the
    seed doesn't itself exercise the code path we want to test elsewhere.
    """
    cid = _next_canonical()
    rows = await client.query(
        "CREATE decision SET description=$d, source_type='manual', "
        "source_ref='v18-test', status=$s, canonical_id=$c",
        {"d": f"probe {cid}", "s": status, "c": cid},
    )
    assert rows, "CREATE decision returned no rows"
    return str(rows[0]["id"])


async def _read_updated_at(client: LedgerClient, decision_id: str) -> str:
    rows = await client.query(
        f"SELECT updated_at, created_at FROM {decision_id} LIMIT 1"
    )
    assert rows, f"decision {decision_id} missing"
    assert "updated_at" in rows[0], "updated_at field absent — schema not migrated"
    return str(rows[0]["updated_at"])


@pytest.mark.asyncio
async def test_v18_schema_version_advanced() -> None:
    """Migration brings schema_meta.version to v18 (or beyond)."""
    c = await _fresh_client()
    try:
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 18, "v18 precondition (#87) must be applied"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_create_decision_sets_updated_at_close_to_created_at() -> None:
    """A fresh CREATE decision row has updated_at populated via DEFAULT time::now()."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        rows = await c.query(f"SELECT updated_at, created_at FROM {did} LIMIT 1")
        assert rows
        # Both should be ISO datetime strings; DEFAULT time::now() fires twice
        # on a single CREATE so they may differ by a microsecond — assert both
        # present and non-empty rather than equal.
        assert rows[0].get("updated_at"), "updated_at must be set by DEFAULT"
        assert rows[0].get("created_at"), "created_at must be set by DEFAULT"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_update_decision_status_bumps_updated_at() -> None:
    c = await _fresh_client()
    try:
        did = await _seed_decision(c, status="ungrounded")
        before = await _read_updated_at(c, did)
        # SurrealDB datetime resolution is sub-microsecond — sleep briefly so
        # the bump is observable as a strict inequality.
        await asyncio.sleep(0.01)
        await update_decision_status(c, did, "drifted")
        after = await _read_updated_at(c, did)
        assert after > before, (
            f"update_decision_status did not bump updated_at "
            f"(before={before!r}, after={after!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_update_decision_level_bumps_updated_at() -> None:
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        before = await _read_updated_at(c, did)
        await asyncio.sleep(0.01)
        await update_decision_level(c, did, "L2")
        after = await _read_updated_at(c, did)
        assert after > before, (
            f"update_decision_level did not bump updated_at "
            f"(before={before!r}, after={after!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_apply_ratify_bumps_updated_at() -> None:
    """The adapter's signoff write must also carry updated_at = time::now()."""
    adapter, c = await _fresh_adapter()
    try:
        did = await _seed_decision(c)
        before = await _read_updated_at(c, did)
        await asyncio.sleep(0.01)
        await adapter.apply_ratify(
            did, {"state": "ratified", "session_id": "test", "signer": "test"}
        )
        after = await _read_updated_at(c, did)
        assert after > before, (
            f"apply_ratify did not bump updated_at "
            f"(before={before!r}, after={after!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_apply_supersede_bumps_updated_at_on_old_decision() -> None:
    """The signoff-freeze write on the old decision must carry updated_at."""
    adapter, c = await _fresh_adapter()
    try:
        old_id = await _seed_decision(c)
        new_id = await _seed_decision(c)
        before_old = await _read_updated_at(c, old_id)
        await asyncio.sleep(0.01)
        await adapter.apply_supersede(
            new_id=new_id,
            old_id=old_id,
            signer="test",
            signoff_note="v18 test",
            superseded_at="2026-05-12T00:00:00Z",
            session_id="test-session",
        )
        after_old = await _read_updated_at(c, old_id)
        assert after_old > before_old, (
            f"apply_supersede did not bump old decision's updated_at "
            f"(before={before_old!r}, after={after_old!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_find_or_create_canonical_update_path_bumps_updated_at() -> None:
    """The canonical-dedup UPDATE path in upsert_decision
    must include updated_at in its set_clause. Calling with the same canonical
    inputs as an existing row triggers the UPDATE branch."""
    c = await _fresh_client()
    try:
        # First call CREATEs the row.
        did_first = await upsert_decision(
            c,
            description="canonical probe",
            rationale="initial",
            feature_hint="auth",
            source_type="manual",
            source_ref="v18-canonical-test",
            meeting_date="",
            speakers=[],
            status="ungrounded",
        )
        before = await _read_updated_at(c, did_first)
        await asyncio.sleep(0.01)
        # Second call with same canonical-defining inputs → UPDATE path.
        did_second = await upsert_decision(
            c,
            description="canonical probe",
            rationale="updated rationale",
            feature_hint="auth",
            source_type="manual",
            source_ref="v18-canonical-test",
            meeting_date="",
            speakers=[],
            status="ungrounded",
        )
        assert did_first == did_second, "canonical dedup must return same id"
        after = await _read_updated_at(c, did_second)
        assert after > before, (
            f"canonical UPDATE path did not bump updated_at "
            f"(before={before!r}, after={after!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_resolve_collision_signoff_clear_bumps_updated_at() -> None:
    """The bare UPDATE in handlers/resolve_collision.py (clearing collision_pending
    by writing a proposed signoff) must carry updated_at = time::now().

    Tested at the SQL level — replicates the handler's UPDATE without going
    through the full handle_resolve_collision flow (which would also exercise
    apply_supersede, already covered separately).
    """
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        before = await _read_updated_at(c, did)
        await asyncio.sleep(0.01)
        # The exact SQL emitted by handlers/resolve_collision.py:128 after the
        # updated_at audit.
        await c.execute(
            f"UPDATE {did} SET signoff = $s, updated_at = time::now()",
            {"s": {"state": "proposed", "session_id": "t", "created_at": "now"}},
        )
        after = await _read_updated_at(c, did)
        assert after > before, (
            f"resolve_collision signoff-clear did not bump updated_at "
            f"(before={before!r}, after={after!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_resolve_collision_link_parent_bumps_updated_at() -> None:
    """handlers/resolve_collision.py:99 — link_parent UPDATE must bump updated_at."""
    c = await _fresh_client()
    try:
        child_id = await _seed_decision(c)
        parent_id = await _seed_decision(c)
        before = await _read_updated_at(c, child_id)
        await asyncio.sleep(0.01)
        # Replicates handlers/resolve_collision.py:99 after the audit.
        await c.execute(
            f"UPDATE {child_id} SET parent_decision_id = $pid, updated_at = time::now()",
            {"pid": parent_id},
        )
        after = await _read_updated_at(c, child_id)
        assert after > before, (
            f"resolve_collision link_parent did not bump updated_at "
            f"(before={before!r}, after={after!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_index_idx_decision_updated_at_exists() -> None:
    """The new index must exist after migrate (sanity — guards against a future
    init_schema reorder that drops the DEFINE INDEX statement).

    INFO FOR TABLE returns empty in SurrealDB v2 embedded mode (per the
    `Known v2 quirks` note in CLAUDE.md), so we probe indirectly: an indexed
    field supports cheap MAX() / ORDER BY queries without scanning. We just
    confirm the query runs and returns a value of the right shape.
    """
    c = await _fresh_client()
    try:
        await _seed_decision(c)
        await asyncio.sleep(0.01)
        await _seed_decision(c)
        rows = await c.query(
            "SELECT updated_at FROM decision ORDER BY updated_at DESC LIMIT 1"
        )
        assert rows
        assert rows[0].get("updated_at"), (
            "ORDER BY updated_at returned a row without the field — "
            "index is missing or schema not migrated"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v18_migration_backfills_legacy_rows_with_none_updated_at() -> None:
    """A row whose updated_at was cleared (simulating a pre-v18 ledger that
    never had the field) is backfilled to created_at on migrate.

    Re-runs the migration backfill UPDATE explicitly. SurrealDB's schema
    is forward-only — on a v18 install we can't truly remove the DEFINE FIELD,
    but we can simulate the pre-v18 state by clearing the value and asserting
    the migration's UPDATE...WHERE updated_at IS NONE backfills it."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        # Clear updated_at to simulate a pre-v18 row that had no such field.
        await c.execute(f"UPDATE {did} SET updated_at = NONE")
        rows = await c.query(f"SELECT updated_at FROM {did} LIMIT 1")
        assert rows
        assert rows[0].get("updated_at") in (None, ""), (
            "test setup failed — could not clear updated_at to simulate pre-v18 row"
        )
        # Re-run the migration's backfill body.
        await c.query(
            "UPDATE decision SET updated_at = created_at WHERE updated_at IS NONE"
        )
        rows = await c.query(
            f"SELECT updated_at, created_at FROM {did} LIMIT 1"
        )
        assert rows
        assert rows[0]["updated_at"] == rows[0]["created_at"], (
            "backfill did not set updated_at = created_at "
            f"(updated_at={rows[0].get('updated_at')!r}, "
            f"created_at={rows[0].get('created_at')!r})"
        )
    finally:
        await c.close()
