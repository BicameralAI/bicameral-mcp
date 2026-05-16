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
    # v15→v16 body is still a no-op (just bumps schema_meta.version). The
    # singleton row on bicameral_meta is created lazily by
    # ``adapter.connect()`` → ``_write_wire_format_sentinel`` for v16, and
    # eagerly by ``_migrate_v18_to_v19`` for v19+ (so the
    # ``decision_revision_bump`` event has somewhere to bump). At
    # SCHEMA_VERSION ≥ 19 the row must exist post-migrate with
    # ``decision_revision`` initialized to 0; at v16–v18 it stays empty.
    if SCHEMA_VERSION >= 19:
        assert len(bm_rows) == 1, (
            f"v19+ migrate must create the bicameral_meta singleton, got {bm_rows!r}"
        )
        assert bm_rows[0].get("decision_revision") == 0, (
            f"singleton must initialize decision_revision=0, got {bm_rows[0]!r}"
        )
    else:
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


async def test_migrate_v18_to_v19_backfills_decision_revision_on_preexisting_row(
    fresh_client,
):
    """Regression: pre-v19 ``bicameral_meta`` rows (written by
    ``_write_wire_format_sentinel`` on first connect) must have
    ``decision_revision`` backfilled to 0 by the v18→v19 migration.

    The original v0.13.x migration only seeded the row when the table
    was empty (``if not rows: CREATE``). If a sentinel row already
    existed, the seed branch was skipped and ``decision_revision``
    stayed NONE forever because SurrealDB v2's ``DEFINE FIELD ... DEFAULT
    0`` doesn't backfill existing rows. Every subsequent decision
    UPDATE then blew up the ``decision_revision_bump`` event with
    "Cannot perform addition with 'NONE' and '1'", which the
    ``_migrate_v22_to_v23`` per-row try/except silently swallowed —
    causing the classification migration to "succeed" while skipping
    every legacy row.

    Simulate that state and assert v19 (and v23 defense-in-depth)
    rescue it.
    """
    # 1. Set up a DB that looks like "post-v18, pre-v19 sentinel-row state":
    #    schema_meta=18, bicameral_meta has a sentinel row whose
    #    decision_revision is NONE.
    #
    # The real-world failure mode: the row was CREATEd by
    # ``_write_wire_format_sentinel`` (v16) BEFORE the
    # ``decision_revision`` field was ever defined. SurrealDB v2's
    # ``DEFAULT 0`` only fires on subsequent CREATEs; existing rows
    # keep whatever state they had (NONE). We can't replay that exact
    # ordering after init_schema has already DEFINEd the field — so
    # we REMOVE FIELD to drop the constraint, CREATE the row in the
    # constraint-less state, then re-DEFINE the field. The end state
    # is identical to the real bug: a row whose ``decision_revision``
    # is NONE because it was never touched after the field landed.
    await init_schema(fresh_client)
    await fresh_client.execute("DELETE FROM schema_meta")
    await fresh_client.execute(
        "CREATE schema_meta SET version = $v, migrated_at = time::now()", {"v": 18}
    )
    await fresh_client.execute("DELETE FROM bicameral_meta")
    await fresh_client.execute("REMOVE FIELD decision_revision ON bicameral_meta")
    await fresh_client.execute(
        "CREATE bicameral_meta SET "
        "surrealdb_client_version_at_first_write = '2.0.0', "
        "surrealdb_client_version_at_last_write = '2.0.0', "
        "last_write_at = time::now()"
    )
    # Re-define the field — DEFAULT 0 won't backfill the existing row.
    await fresh_client.execute(
        "DEFINE FIELD decision_revision ON bicameral_meta TYPE int DEFAULT 0"
    )
    pre_rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert len(pre_rows) == 1
    assert pre_rows[0].get("decision_revision") is None, (
        f"setup precondition: row must have NONE decision_revision, got {pre_rows[0]!r}"
    )

    # 2. Run migrate. v18→v19 must backfill decision_revision = 0.
    await migrate(fresh_client, allow_destructive=True)

    post_rows = await fresh_client.query("SELECT * FROM bicameral_meta")
    assert len(post_rows) == 1, (
        f"migrate must keep exactly one bicameral_meta row, got {post_rows!r}"
    )
    assert post_rows[0].get("decision_revision") == 0, (
        f"v18→v19 must backfill decision_revision=0 on existing rows; "
        f"got {post_rows[0]!r}"
    )

    # 3. Trigger contract: a decision UPDATE must increment the counter
    # (i.e., NONE + 1 no longer blows up). This is the load-bearing
    # invariant — without backfill, every UPDATE fails and the
    # downstream v22→v23 silently skips.
    await fresh_client.execute(
        "CREATE decision SET description = 'probe', source_type = 'manual', "
        "canonical_id = 'probe-cid', status = 'ungrounded'"
    )
    after_create = await fresh_client.query(
        "SELECT decision_revision FROM bicameral_meta LIMIT 1"
    )
    assert after_create[0]["decision_revision"] >= 1, (
        "decision_revision_bump event must increment counter on decision CREATE; "
        f"got {after_create[0]!r}"
    )
