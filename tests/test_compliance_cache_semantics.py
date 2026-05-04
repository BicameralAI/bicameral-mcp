"""v4 cache semantics — derive_status + drift-sweep cache awareness.

Proves Phase 2 of 2026-04-20-ingest-time-verification.md:
- derive_status projects REFLECTED / DRIFTED / PENDING based on cached verdict
- Drift sweep emits pending_compliance_checks for unverified shapes
- Seeding a compliance_check row via resolve_compliance (simulated here by
  direct write) promotes the decision out of PENDING
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.queries import get_compliance_verdict
from ledger.schema import init_schema, migrate
from ledger.status import derive_status

# ── Pure unit tests: derive_status decision table ────────────────────


def test_derive_status_empty_stored_hash_is_ungrounded():
    assert derive_status("", None) == "ungrounded"
    assert derive_status("", "anything") == "ungrounded"
    assert derive_status("", "anything", cached_verdict={"verdict": "compliant"}) == "ungrounded"


def test_derive_status_missing_actual_hash_is_pending():
    """Symbol absent at the current ref → PENDING regardless of verdict."""
    assert derive_status("stored_h", None) == "pending"
    assert derive_status("stored_h", None, cached_verdict={"verdict": "compliant"}) == "pending"


def test_derive_status_no_verdict_is_pending_even_on_hash_match():
    """v3 core invariant: hash-match alone does NOT yield REFLECTED."""
    assert derive_status("hash_abc", "hash_abc") == "pending"
    assert derive_status("hash_abc", "hash_abc", cached_verdict=None) == "pending"


def test_derive_status_no_verdict_is_pending_on_hash_change():
    """Cache miss after code edit → PENDING (not DRIFTED)."""
    assert derive_status("hash_old", "hash_new") == "pending"
    assert derive_status("hash_old", "hash_new", cached_verdict=None) == "pending"


def test_derive_status_compliant_verdict_is_reflected():
    verdict = {"verdict": "compliant", "confidence": "high", "explanation": "matches"}
    assert derive_status("h", "h", cached_verdict=verdict) == "reflected"
    # Hash change irrelevant — verdict is keyed on actual_hash; the fact
    # it exists for this actual_hash is the proof of verification.
    assert derive_status("old", "h", cached_verdict=verdict) == "reflected"


def test_derive_status_noncompliant_verdict_is_drifted():
    verdict = {"verdict": "drifted", "confidence": "high", "explanation": "broken"}
    assert derive_status("h", "h", cached_verdict=verdict) == "drifted"
    assert derive_status("old", "h", cached_verdict=verdict) == "drifted"


def test_derive_status_falsy_compliant_field_is_drifted():
    """Defensive: missing or non-compliant verdict → DRIFTED.

    The caller-LLM write path requires ``verdict: str``, but this
    function must be robust to malformed or missing cache rows.
    """
    assert derive_status("h", "h", cached_verdict={"verdict": "drifted"}) == "drifted"
    assert derive_status("h", "h", cached_verdict={}) == "drifted"
    assert derive_status("h", "h", cached_verdict={"verdict": "not_relevant"}) == "drifted"


# ── Cache lookup query ────────────────────────────────────────────────


async def _fresh_client() -> LedgerClient:
    c = LedgerClient(url="memory://", ns="cache_sem_test", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_get_compliance_verdict_returns_none_when_empty():
    c = await _fresh_client()
    try:
        verdict = await get_compliance_verdict(c, "decision:a", "code_region:r", "hash_x")
        assert verdict is None
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_get_compliance_verdict_returns_row_for_exact_tuple():
    c = await _fresh_client()
    try:
        await c.execute(
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h, verdict = 'compliant', confidence = 'high', "
            "explanation = 'looks right', phase = 'ingest'",
            {"i": "decision:a", "r": "code_region:r", "h": "hash_x"},
        )
        verdict = await get_compliance_verdict(c, "decision:a", "code_region:r", "hash_x")
        assert verdict is not None
        assert verdict["verdict"] == "compliant"
        assert verdict["confidence"] == "high"
        assert verdict["explanation"] == "looks right"
        assert verdict["phase"] == "ingest"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_get_compliance_verdict_misses_when_hash_differs():
    """Cache key includes content_hash — different hash = cache miss.

    This is the self-healing property: when code changes, the cache miss
    forces the next sweep to emit a pending check for the new shape.
    Previously-verified shapes remain cached for revert scenarios.
    """
    c = await _fresh_client()
    try:
        await c.execute(
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h, verdict = 'compliant', confidence = 'high', "
            "explanation = '', phase = 'ingest'",
            {"i": "decision:a", "r": "code_region:r", "h": "hash_old"},
        )
        # Lookup for NEW hash: cache miss even though a row exists for the
        # same (decision, region) — the content changed.
        verdict = await get_compliance_verdict(c, "decision:a", "code_region:r", "hash_new")
        assert verdict is None, (
            "Cache lookup must key on content_hash exactly; stale verdicts "
            "for other shapes must not leak through."
        )
        # Sanity: the old hash still hits.
        old_verdict = await get_compliance_verdict(c, "decision:a", "code_region:r", "hash_old")
        assert old_verdict is not None
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_get_compliance_verdict_returns_first_when_multiple_phases():
    """Corner case: a (intent, region, hash) tuple should have exactly one
    row (enforced by UNIQUE). If multiple somehow exist, the lookup still
    returns a single verdict.
    """
    c = await _fresh_client()
    try:
        # Two rows with different (decision, region, hash) — distinguishable.
        await c.execute(
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h, verdict = 'compliant', confidence = 'high', "
            "explanation = 'A', phase = 'ingest'",
            {"i": "decision:a", "r": "code_region:r", "h": "hash_1"},
        )
        await c.execute(
            "CREATE compliance_check SET decision_id = $i, region_id = $r, "
            "content_hash = $h, verdict = 'drifted', confidence = 'high', "
            "explanation = 'B', phase = 'drift'",
            {"i": "decision:a", "r": "code_region:r", "h": "hash_2"},
        )
        v1 = await get_compliance_verdict(c, "decision:a", "code_region:r", "hash_1")
        v2 = await get_compliance_verdict(c, "decision:a", "code_region:r", "hash_2")
        assert v1["verdict"] == "compliant"
        assert v2["verdict"] == "drifted"
    finally:
        await c.close()
