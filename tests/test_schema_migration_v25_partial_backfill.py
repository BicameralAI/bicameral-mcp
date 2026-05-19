"""#405 — v24→v25 backfill: rewrite never-compliant 'drifted' rows to 'partial'.

The migration must:
  1. Extend the verdict ASSERT enum to include 'partial'.
  2. For every (decision_id, region_id) pair whose only history is 'drifted'
     verdicts (no 'compliant' rows ever), rewrite those drifted rows to
     'partial'.
  3. Leave 'drifted' rows alone when a prior 'compliant' verdict exists for
     the same (decision_id, region_id) — those are real regressions.
  4. Be idempotent: re-running finds nothing to convert.

This test seeds a v24-shape database by hand (running init_schema + migrate
to the current version, then mutating compliance_check directly into a state
that simulates the pre-#405 caller-LLM habit) and re-invokes the v25
migration to confirm the backfill behaves correctly.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.schema import _migrate_v24_to_v25, init_schema, migrate


async def _fresh_client(ns: str = "v25_backfill") -> LedgerClient:
    c = LedgerClient(url="memory://", ns=ns, db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_check(
    client: LedgerClient,
    *,
    decision_id: str,
    region_id: str,
    content_hash: str,
    verdict: str,
) -> None:
    await client.execute(
        "CREATE compliance_check SET decision_id = $d, region_id = $r, "
        "content_hash = $h, verdict = $v, confidence = 'high', "
        "explanation = '', phase = 'drift'",
        {"d": decision_id, "r": region_id, "h": content_hash, "v": verdict},
    )


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_backfill_rewrites_never_compliant_drifted_to_partial():
    """The acceptance fixture from the #405 issue: 3 'drifted' rows seeded
    across two (decision, region) pairs. The pair with a prior compliant
    keeps its drifted; the never-compliant pair gets rewritten to partial.
    """
    c = await _fresh_client(ns="backfill_basic")
    try:
        # Pair A — has a prior compliant row, so its drifted is a real regression.
        await _seed_check(
            c, decision_id="decision:a", region_id="code_region:a",
            content_hash="hash_a1", verdict="compliant",
        )
        await _seed_check(
            c, decision_id="decision:a", region_id="code_region:a",
            content_hash="hash_a2", verdict="drifted",
        )

        # Pair B — only drifted rows, never compliant. Two of them at distinct
        # hashes (so the UNIQUE cache-key index doesn't reject the second).
        await _seed_check(
            c, decision_id="decision:b", region_id="code_region:b",
            content_hash="hash_b1", verdict="drifted",
        )
        await _seed_check(
            c, decision_id="decision:b", region_id="code_region:b",
            content_hash="hash_b2", verdict="drifted",
        )

        # Pair C — never-compliant, only drifted (single row).
        await _seed_check(
            c, decision_id="decision:c", region_id="code_region:c",
            content_hash="hash_c1", verdict="drifted",
        )

        # Re-run the v25 migration directly (the version bump already ran via
        # _fresh_client's migrate(); this exercises the backfill logic again,
        # which must be idempotent and additionally must process the just-
        # seeded rows on first pass).
        await _migrate_v24_to_v25(c)

        rows = await c.query(
            "SELECT decision_id, content_hash, verdict FROM compliance_check "
            "ORDER BY decision_id, content_hash"
        )
        by_hash = {r["content_hash"]: r["verdict"] for r in rows}

        # Pair A: compliant stays compliant; drifted stays drifted (prior compliant exists).
        assert by_hash["hash_a1"] == "compliant"
        assert by_hash["hash_a2"] == "drifted"

        # Pair B: both drifted rows rewritten to partial (no prior compliant).
        assert by_hash["hash_b1"] == "partial"
        assert by_hash["hash_b2"] == "partial"

        # Pair C: single drifted rewritten to partial.
        assert by_hash["hash_c1"] == "partial"

        # Aggregate sanity — drift-alarm count drops from 3 to 1.
        drift_count = await c.query(
            "SELECT count() AS n FROM compliance_check WHERE verdict = 'drifted' GROUP ALL"
        )
        partial_count = await c.query(
            "SELECT count() AS n FROM compliance_check WHERE verdict = 'partial' GROUP ALL"
        )
        assert int(drift_count[0]["n"]) == 1
        assert int(partial_count[0]["n"]) == 3
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_backfill_is_idempotent():
    """Re-running the v25 migration must not flap rows back and forth."""
    c = await _fresh_client(ns="backfill_idempotent")
    try:
        await _seed_check(
            c, decision_id="decision:idem", region_id="code_region:idem",
            content_hash="hash_only", verdict="drifted",
        )

        await _migrate_v24_to_v25(c)  # first pass — converts to partial
        await _migrate_v24_to_v25(c)  # second pass — must be a no-op
        await _migrate_v24_to_v25(c)  # third pass — must still be a no-op

        rows = await c.query("SELECT verdict FROM compliance_check")
        assert len(rows) == 1
        assert rows[0]["verdict"] == "partial"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_partial_verdict_accepted_by_extended_assert():
    """After migration the verdict ASSERT must accept 'partial' as a value.
    This is the schema-level acceptance criterion from #405."""
    c = await _fresh_client(ns="assert_partial")
    try:
        # No exception — the extended enum accepts 'partial'.
        await _seed_check(
            c, decision_id="decision:p", region_id="code_region:p",
            content_hash="hash_p", verdict="partial",
        )
        rows = await c.query("SELECT verdict FROM compliance_check")
        assert rows[0]["verdict"] == "partial"
    finally:
        await c.close()
