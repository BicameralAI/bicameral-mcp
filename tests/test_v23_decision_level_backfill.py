"""v22 → v23 migration: backfill decision_level for legacy decisions.

Sociable tests — real SurrealDB adapter over ``memory://``, real schema
init + migrate. Seeds decisions with various source_type and binding
states, then runs ``_migrate_v22_to_v23`` directly to simulate the
backfill on legacy rows whose decision_level was cleared to NONE.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import SCHEMA_VERSION, _migrate_v22_to_v23, init_schema, migrate

_NS_COUNTER = 0


async def _fresh_client() -> LedgerClient:
    global _NS_COUNTER
    _NS_COUNTER += 1
    c = LedgerClient(url="memory://", ns=f"v23_test_{_NS_COUNTER}", db="ledger_v23_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_decision(
    c: LedgerClient,
    *,
    description: str,
    source_type: str = "manual",
    decision_level: str | None = None,
    canonical_id: str = "",
) -> str:
    """Insert a decision and return its string id."""
    params: dict = {
        "d": description,
        "st": source_type,
        "cid": canonical_id or f"cid-{description}",
    }
    if decision_level is not None:
        rows = await c.query(
            "CREATE decision SET description = $d, source_type = $st, "
            "canonical_id = $cid, status = 'ungrounded', decision_level = $lvl",
            {**params, "lvl": decision_level},
        )
    else:
        rows = await c.query(
            "CREATE decision SET description = $d, source_type = $st, "
            "canonical_id = $cid, status = 'ungrounded'",
            params,
        )
    row = rows[0]
    rid = row.get("id")
    if isinstance(rid, dict):
        return f"decision:{rid.get('id', rid)}"
    return str(rid)


async def _seed_bound_decision(
    c: LedgerClient,
    *,
    description: str,
    source_type: str = "manual",
    canonical_id: str = "",
) -> str:
    """Insert a decision with a binds_to edge to a code_region (no decision_level)."""
    did = await _seed_decision(
        c,
        description=description,
        source_type=source_type,
        canonical_id=canonical_id,
    )
    await c.query(
        "CREATE code_region SET file_path = $fp, symbol_name = $sn, "
        "start_line = 1, end_line = 10, content_hash = 'abc123'",
        {"fp": f"src/{description}.py", "sn": f"Sym_{description}"},
    )
    regions = await c.query(
        "SELECT type::string(id) AS id FROM code_region WHERE file_path = $fp",
        {"fp": f"src/{description}.py"},
    )
    rid = regions[0]["id"]
    await c.execute(f"RELATE {did}->binds_to->{rid} SET confidence = 0.9, created_at = time::now()")
    return did


async def _get_level(c: LedgerClient, did: str) -> str | None:
    rows = await c.query(f"SELECT decision_level FROM {did} LIMIT 1")
    if not rows:
        return None
    return rows[0].get("decision_level")


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_schema_version() -> None:
    """After migrate, schema version is >= 23."""
    c = await _fresh_client()
    try:
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 23
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_bound_decisions_become_l2() -> None:
    """Decisions with binds_to edges are classified as L2."""
    c = await _fresh_client()
    try:
        did = await _seed_bound_decision(c, description="bound-arch")
        await c.execute(f"UPDATE {did} SET decision_level = NONE")
        assert await _get_level(c, did) is None

        await _migrate_v22_to_v23(c)

        assert await _get_level(c, did) == "L2"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_product_source_becomes_l1() -> None:
    """Unbound decisions from product sources are classified as L1."""
    c = await _fresh_client()
    try:
        for st in ("transcript", "notion", "slack", "document"):
            did = await _seed_decision(c, description=f"product-{st}", source_type=st)
            await c.execute(f"UPDATE {did} SET decision_level = NONE")
            assert await _get_level(c, did) is None

        await _migrate_v22_to_v23(c)

        for st in ("transcript", "notion", "slack", "document"):
            rows = await c.query(
                "SELECT type::string(id) AS id, decision_level FROM decision "
                "WHERE description = $d",
                {"d": f"product-{st}"},
            )
            assert rows, f"missing row for source_type={st}"
            assert rows[0]["decision_level"] == "L1", (
                f"expected L1 for source_type={st}, got {rows[0]['decision_level']}"
            )
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_impl_source_becomes_l3() -> None:
    """Unbound decisions from implementation sources are classified as L3."""
    c = await _fresh_client()
    try:
        for st in ("implementation_choice", "agent_session"):
            did = await _seed_decision(c, description=f"impl-{st}", source_type=st)
            await c.execute(f"UPDATE {did} SET decision_level = NONE")
            assert await _get_level(c, did) is None

        await _migrate_v22_to_v23(c)

        for st in ("implementation_choice", "agent_session"):
            rows = await c.query(
                "SELECT type::string(id) AS id, decision_level FROM decision "
                "WHERE description = $d",
                {"d": f"impl-{st}"},
            )
            assert rows, f"missing row for source_type={st}"
            assert rows[0]["decision_level"] == "L3", (
                f"expected L3 for source_type={st}, got {rows[0]['decision_level']}"
            )
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_unknown_source_becomes_l2() -> None:
    """Unbound decisions with unknown source_type default to L2."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(c, description="unknown-src", source_type="manual")
        await c.execute(f"UPDATE {did} SET decision_level = NONE")
        assert await _get_level(c, did) is None

        await _migrate_v22_to_v23(c)

        assert await _get_level(c, did) == "L2"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_does_not_overwrite_existing_level() -> None:
    """Decisions that already have a decision_level are not touched."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(
            c,
            description="already-classified",
            source_type="transcript",
            decision_level="L2",
        )
        assert await _get_level(c, did) == "L2"

        await _migrate_v22_to_v23(c)

        assert await _get_level(c, did) == "L2"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_idempotent() -> None:
    """Running the backfill twice is a no-op."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(c, description="idempotent-probe", source_type="notion")
        await c.execute(f"UPDATE {did} SET decision_level = NONE")
        await _migrate_v22_to_v23(c)
        assert await _get_level(c, did) == "L1"

        await _migrate_v22_to_v23(c)
        assert await _get_level(c, did) == "L1"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_v23_bound_product_source_still_l2() -> None:
    """A product-source decision WITH bindings should be L2 (code-grounded
    takes priority over source_type)."""
    c = await _fresh_client()
    try:
        did = await _seed_bound_decision(c, description="bound-product", source_type="transcript")
        await c.execute(f"UPDATE {did} SET decision_level = NONE")
        assert await _get_level(c, did) is None

        await _migrate_v22_to_v23(c)

        assert await _get_level(c, did) == "L2"
    finally:
        await c.close()
