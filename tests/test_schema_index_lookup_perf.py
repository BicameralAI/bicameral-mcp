"""Equality-key indexes on symbol.name + vocab_cache.(query_text, repo).

Today's bug: both fields are indexed only via SEARCH/BM25, which accelerates
`@0@` semantic match but NOT `WHERE field = $value` equality. The UPSERT call
sites in `ledger/queries.py` (``upsert_symbol``, vocab_cache UPSERT) fall back
to full table scans; latency grows linearly and the 5.0s read budget breaks
near the end of large ``reset --replay-from-events`` runs (#410 dogfood).

Verification mechanism: SurrealDB 2.x's trailing ``EXPLAIN`` modifier returns
a parseable query-plan row sequence. Pre-migration, `WHERE name = 'x'` plans
to ``Iterate Table`` (full scan); post-migration the new ``idx_sym_name_lookup``
makes it plan to ``Iterate Index``. Empirically validated against
``memory://`` SurrealDB during the audit (AUDIT_REPORT.md, R2).

Sociable per ``pilot/mcp/CLAUDE.md`` § "Sociable Testing for UX Paths": no
``MagicMock``; no hand-crafted row dicts; the real ledger writes through the
real schema.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.queries import upsert_symbol
from ledger.schema import (
    _MIGRATIONS,
    SCHEMA_VERSION,
    _migrate_v24_to_v25,
    init_schema,
    migrate,
)


@pytest.fixture
async def fresh_client():
    client = LedgerClient("memory://")
    await client.connect()
    await init_schema(client)
    await migrate(client)
    yield client
    await client.close()


async def test_upsert_symbol_returns_single_row_for_unique_name(fresh_client):
    """UPSERT semantics: novel name → exactly one row inserted, id returned."""
    for i in range(1000):
        await fresh_client.execute(
            "CREATE symbol SET name=$n, file_path=$fp, sym_type='function'",
            {"n": f"seeded_symbol_{i:04d}", "fp": f"seed_file_{i % 50}.py"},
        )

    sym_id = await upsert_symbol(
        fresh_client,
        name="unique_marker_x",
        file_path="src/module.py",
        sym_type="function",
    )
    assert sym_id, "upsert_symbol must return a non-empty id"

    rows = await fresh_client.query(
        "SELECT id FROM symbol WHERE name = $n", {"n": "unique_marker_x"}
    )
    assert len(rows) == 1, f"expected exactly 1 matching row, got {len(rows)}: {rows!r}"


async def test_upsert_vocab_cache_returns_single_row_for_unique_compound_key(
    fresh_client,
):
    """UPSERT semantics on vocab_cache compound key: novel (query_text, repo)
    → exactly one row, no duplicates."""
    for i in range(1000):
        await fresh_client.execute(
            "CREATE vocab_cache SET query_text=$q, repo=$r, symbols=[], hit_count=0",
            {"q": f"seeded query {i:04d}", "r": "/repo/a"},
        )

    await fresh_client.execute(
        """
        UPSERT vocab_cache SET
            query_text = $query_text,
            repo       = $repo,
            symbols    = $symbols,
            hit_count  = 1,
            last_hit   = time::now()
        WHERE query_text = $query_text AND repo = $repo
        """,
        {
            "query_text": "novel marker query",
            "repo": "/repo/marker",
            "symbols": ["marker_symbol"],
        },
    )

    rows = await fresh_client.query(
        "SELECT id FROM vocab_cache WHERE query_text = $q AND repo = $r",
        {"q": "novel marker query", "r": "/repo/marker"},
    )
    assert len(rows) == 1, f"expected exactly 1 matching row, got {len(rows)}: {rows!r}"


async def test_symbol_name_lookup_uses_equality_index_post_migration(fresh_client):
    """Post-migration the query plan for ``WHERE name = $x`` must select
    ``idx_sym_name_lookup`` rather than fall back to a full table scan.

    Fail mode the test catches: ``_migrate_v24_to_v25`` runs without exception
    but ``_execute_define_idempotent`` silently swallows the DEFINE INDEX. The
    query plan stays at ``Iterate Table`` and this assertion fails loudly.
    """
    plan = await fresh_client.query("SELECT * FROM symbol WHERE name = 'probe' EXPLAIN")
    assert plan, f"EXPLAIN returned no rows: {plan!r}"
    head = plan[0]
    assert head.get("operation") == "Iterate Index", (
        f"symbol.name lookup is not using an index — query plan reports "
        f"{head.get('operation')!r}. Full plan: {plan!r}. "
        f"This means ``idx_sym_name_lookup`` did not land — either the "
        f"v25 migration is silently broken or the schema DEFINE INDEX "
        f"was not applied."
    )
    detail = head.get("detail") or {}
    plan_detail = detail.get("plan") or {}
    assert plan_detail.get("index") == "idx_sym_name_lookup", (
        f"symbol.name lookup is using the wrong index. detail={detail!r}. "
        f"Expected ``idx_sym_name_lookup`` (the equality index added in v25). "
        f"BM25 ``idx_sym_name`` does not accelerate equality lookups."
    )


async def test_vocab_cache_lookup_uses_compound_index_post_migration(fresh_client):
    """Post-migration the query plan for ``WHERE query_text = $q AND repo = $r``
    must select ``idx_vocab_query_lookup``. Same failure-mode coverage as
    the symbol test."""
    plan = await fresh_client.query(
        "SELECT * FROM vocab_cache WHERE query_text = 'probe' AND repo = '/r' EXPLAIN"
    )
    assert plan, f"EXPLAIN returned no rows: {plan!r}"
    head = plan[0]
    assert head.get("operation") == "Iterate Index", (
        f"vocab_cache compound-key lookup is not using an index — query plan "
        f"reports {head.get('operation')!r}. Full plan: {plan!r}."
    )
    detail = head.get("detail") or {}
    plan_detail = detail.get("plan") or {}
    assert plan_detail.get("index") == "idx_vocab_query_lookup", (
        f"vocab_cache compound-key lookup is using the wrong index. "
        f"detail={detail!r}. Expected ``idx_vocab_query_lookup`` "
        f"(the equality index added in v25)."
    )


async def test_schema_version_advances_to_25():
    """``init_schema`` + ``migrate`` on a fresh ledger must record
    schema_meta.version = 25. Loud failure when the migration registry,
    ``SCHEMA_VERSION`` constant, and migration function drift."""
    client = LedgerClient("memory://")
    await client.connect()
    try:
        await init_schema(client)
        await migrate(client)
        rows = await client.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows and rows[0]["version"] == SCHEMA_VERSION, (
            f"schema_meta.version must equal SCHEMA_VERSION ({SCHEMA_VERSION}) "
            f"after init+migrate, got {rows!r}."
        )
        assert _MIGRATIONS[25] is _migrate_v24_to_v25, (
            f"_MIGRATIONS[25] is not the v24→v25 function: {_MIGRATIONS.get(25)!r}"
        )
    finally:
        await client.close()
