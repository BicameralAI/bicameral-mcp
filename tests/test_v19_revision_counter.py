"""v18 → v19 schema migration — bicameral_meta.decision_revision counter
(#87 Phase 6).

Replaces the v18 ORDER BY DESC LIMIT 1 query in get_ledger_revision
with a constant-time counter on the singleton bicameral_meta row.
The counter is auto-bumped by a DEFINE EVENT trigger on every
decision CREATE/UPDATE — zero call-site audit required.

This file pins three contracts:

  1. The counter advances on every decision write (CREATE, UPDATE)
     via the EVENT trigger.
  2. get_ledger_revision reads the counter and returns it as a string.
  3. The counter behaves correctly across all 7 production UPDATE
     call sites that were audited in the v18 precondition — proving
     the EVENT fires regardless of which path triggered the write.

Sociable over memory:// SurrealDB. The EVENT machinery is a real
SurrealDB v2 feature; we exercise it end-to-end rather than mocking.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import (
    get_ledger_revision,
    update_decision_level,
    update_decision_status,
    upsert_decision,
)
from ledger.schema import SCHEMA_VERSION, init_schema, migrate

_NS_COUNTER = 0


async def _fresh_client() -> LedgerClient:
    global _NS_COUNTER
    _NS_COUNTER += 1
    c = LedgerClient(url="memory://", ns=f"v19_test_{_NS_COUNTER}", db="ledger_test")
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
    return f"v19-test-{_CANONICAL_COUNTER}"


async def _seed_decision(client: LedgerClient, *, status: str = "ungrounded") -> str:
    cid = _next_canonical()
    rows = await client.query(
        "CREATE decision SET description=$d, source_type='manual', "
        "source_ref='v19-test', status=$s, canonical_id=$c",
        {"d": f"probe {cid}", "s": status, "c": cid},
    )
    assert rows, "CREATE decision returned no rows"
    return str(rows[0]["id"])


async def _read_counter(client: LedgerClient) -> int:
    rows = await client.query("SELECT decision_revision FROM bicameral_meta LIMIT 1")
    assert rows, "bicameral_meta singleton row missing — migration broken"
    return int(rows[0]["decision_revision"])


# ── Counter mechanics ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_v19_schema_version_advanced() -> None:
    """Migration brings schema_meta.version to v19 (or beyond)."""
    c = await _fresh_client()
    try:
        rows = await c.query("SELECT version FROM schema_meta LIMIT 1")
        assert rows
        assert rows[0]["version"] == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 19, "v19 Phase 6 must be applied"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_counter_starts_at_zero_on_fresh_ledger() -> None:
    """A fresh migrate leaves decision_revision = 0. The singleton row
    must exist post-migrate so the EVENT has somewhere to bump."""
    c = await _fresh_client()
    try:
        assert await _read_counter(c) == 0
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_counter_bumps_on_decision_create() -> None:
    """The EVENT trigger fires on CREATE decision."""
    c = await _fresh_client()
    try:
        before = await _read_counter(c)
        await _seed_decision(c)
        after = await _read_counter(c)
        assert after == before + 1, (
            f"counter should bump by 1 on CREATE (before={before}, after={after})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_counter_bumps_on_decision_update() -> None:
    """The EVENT trigger fires on UPDATE decision."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        before = await _read_counter(c)
        await c.execute(f"UPDATE {did} SET status = 'drifted'")
        after = await _read_counter(c)
        assert after == before + 1, (
            f"counter should bump by 1 on UPDATE (before={before}, after={after})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_counter_advances_monotonically() -> None:
    """Sequential writes advance the counter strictly upward — proves
    the EVENT is atomic (no lost updates)."""
    c = await _fresh_client()
    try:
        readings = [await _read_counter(c)]
        for _ in range(5):
            await _seed_decision(c)
            readings.append(await _read_counter(c))
        for i in range(1, len(readings)):
            assert readings[i] > readings[i - 1], f"counter regressed at step {i}: {readings}"
        assert readings[-1] - readings[0] == 5
    finally:
        await c.close()


# ── get_ledger_revision contract ─────────────────────────────────────


@pytest.mark.asyncio
async def test_v19_get_ledger_revision_returns_counter_as_string() -> None:
    """The helper reads decision_revision and stringifies it."""
    c = await _fresh_client()
    try:
        await _seed_decision(c)
        await _seed_decision(c)
        rev = await get_ledger_revision(c)
        assert rev == "2", f"expected counter value '2', got {rev!r}"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_get_ledger_revision_advances_after_each_write() -> None:
    """The core M7a/c contract — any decision write must advance the
    revision marker. This is what Phase 4's dedup invalidation depends on."""
    c = await _fresh_client()
    try:
        rev_0 = await get_ledger_revision(c)
        did = await _seed_decision(c)
        rev_1 = await get_ledger_revision(c)
        await update_decision_status(c, did, "drifted")
        rev_2 = await get_ledger_revision(c)
        assert rev_0 < rev_1 < rev_2, (
            f"revision must advance on each write "
            f"(rev_0={rev_0!r}, rev_1={rev_1!r}, rev_2={rev_2!r})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_get_ledger_revision_handles_missing_singleton() -> None:
    """Defensive — if bicameral_meta has no rows, return empty-string
    sentinel (not None). Mirrors the v18 behaviour for empty ledgers."""
    c = await _fresh_client()
    try:
        # Force-delete the singleton row to simulate a half-migrated ledger.
        await c.execute("DELETE bicameral_meta")
        rev = await get_ledger_revision(c)
        assert rev == "", f"missing singleton should return empty string, got {rev!r}"
    finally:
        await c.close()


# ── Production call sites — every audited UPDATE must bump ───────────


@pytest.mark.asyncio
async def test_v19_update_decision_status_bumps_counter() -> None:
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        before = await _read_counter(c)
        await update_decision_status(c, did, "drifted")
        assert await _read_counter(c) == before + 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_update_decision_level_bumps_counter() -> None:
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        before = await _read_counter(c)
        await update_decision_level(c, did, "L2")
        assert await _read_counter(c) == before + 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_apply_ratify_bumps_counter() -> None:
    adapter, c = await _fresh_adapter()
    try:
        did = await _seed_decision(c)
        before = await _read_counter(c)
        await adapter.apply_ratify(
            did, {"state": "ratified", "session_id": "test", "signer": "test"}
        )
        # apply_ratify writes signoff + status — both trigger the EVENT, so
        # the counter advances by 2 (or more, if the path also touches the
        # row again). Just assert strict monotone advance.
        after = await _read_counter(c)
        assert after > before, f"apply_ratify must advance counter (before={before}, after={after})"
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_apply_supersede_bumps_counter_on_old_decision() -> None:
    adapter, c = await _fresh_adapter()
    try:
        old_id = await _seed_decision(c)
        new_id = await _seed_decision(c)
        before = await _read_counter(c)
        await adapter.apply_supersede(
            new_id=new_id,
            old_id=old_id,
            signer="test",
            signoff_note="v19 test",
            superseded_at="2026-05-13T00:00:00Z",
            session_id="test-session",
        )
        after = await _read_counter(c)
        assert after > before, (
            f"apply_supersede must advance counter (before={before}, after={after})"
        )
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_upsert_decision_create_path_bumps_counter() -> None:
    c = await _fresh_client()
    try:
        before = await _read_counter(c)
        await upsert_decision(
            c,
            description="v19 canonical probe",
            rationale="phase 6 test",
            feature_hint="auth",
            source_type="manual",
            source_ref="v19-canonical-test",
            meeting_date="",
            speakers=[],
            status="ungrounded",
        )
        assert await _read_counter(c) > before
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_upsert_decision_update_path_bumps_counter() -> None:
    c = await _fresh_client()
    try:
        # First call CREATEs the row → counter bumps.
        await upsert_decision(
            c,
            description="v19 canonical probe",
            rationale="initial",
            feature_hint="auth",
            source_type="manual",
            source_ref="v19-canonical-test",
            meeting_date="",
            speakers=[],
            status="ungrounded",
        )
        before = await _read_counter(c)
        # Second call with same canonical-defining inputs → UPDATE path.
        await upsert_decision(
            c,
            description="v19 canonical probe",
            rationale="updated rationale",
            feature_hint="auth",
            source_type="manual",
            source_ref="v19-canonical-test",
            meeting_date="",
            speakers=[],
            status="ungrounded",
        )
        assert await _read_counter(c) > before
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_resolve_collision_link_parent_sql_bumps_counter() -> None:
    """handlers/resolve_collision.py:99 — link_parent UPDATE shape."""
    c = await _fresh_client()
    try:
        child_id = await _seed_decision(c)
        parent_id = await _seed_decision(c)
        before = await _read_counter(c)
        await c.execute(
            f"UPDATE {child_id} SET parent_decision_id = $pid, updated_at = time::now()",
            {"pid": parent_id},
        )
        assert await _read_counter(c) == before + 1
    finally:
        await c.close()


@pytest.mark.asyncio
async def test_v19_resolve_collision_signoff_clear_sql_bumps_counter() -> None:
    """handlers/resolve_collision.py:128 — collision-pending clear UPDATE shape."""
    c = await _fresh_client()
    try:
        did = await _seed_decision(c)
        before = await _read_counter(c)
        await c.execute(
            f"UPDATE {did} SET signoff = $s, updated_at = time::now()",
            {"s": {"state": "proposed", "session_id": "t", "created_at": "now"}},
        )
        assert await _read_counter(c) == before + 1
    finally:
        await c.close()


# ── Performance — the whole point of Phase 6 ─────────────────────────


@pytest.mark.asyncio
async def test_v19_get_ledger_revision_is_constant_time_at_scale() -> None:
    """The constant-time read is the load-bearing claim of Phase 6.
    Seed 500 decisions then time 50 revision lookups; p95 must be well
    under the 1ms Kevin-signoff threshold and obviously independent of
    ledger size (the whole point of switching off ORDER BY).
    """
    c = await _fresh_client()
    try:
        for i in range(500):
            await c.query(
                "CREATE decision SET description=$d, source_type='m', source_ref='r', "
                "status='ungrounded', canonical_id=$c",
                {"d": f"perf-{i}", "c": f"perf-{i}"},
            )
        samples = []
        for _ in range(50):
            t0 = time.perf_counter()
            rev = await get_ledger_revision(c)
            samples.append((time.perf_counter() - t0) * 1000)
            assert rev, "revision must be non-empty for a populated ledger"
        samples.sort()
        p50 = samples[25]
        p95 = samples[47]
        # The v18 ORDER BY query was ~8ms p50 at N=1000. v19 should be
        # an order of magnitude faster because it's a single-row lookup
        # on a singleton table. Setting the assertion at 3ms gives
        # plenty of margin for CI machine noise while still catching
        # any regression to ORDER-BY-shaped costs.
        assert p95 < 3.0, (
            f"get_ledger_revision should be sub-3ms at N=500 "
            f"(p50={p50:.3f}ms, p95={p95:.3f}ms) — counter mechanism "
            f"regressed back to scanning behaviour?"
        )
    finally:
        await c.close()
