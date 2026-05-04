"""Regression tests for project_decision_status — the authoritative status
deriver introduced in v0.5.0.

v0.6.2 fix: distinguish "never verified" (→ pending) from "was verified,
code has since changed" (→ drifted). Before this fix, every post-verification
code edit silently parked decisions at "pending" forever because the new
content_hash had no cache entry. The session-start banner queried
`status = 'drifted'` and found nothing, so Jacob saw no drift signal.

Closes the gap v0.6.1's session-start banner infra couldn't close on its own.
"""

from __future__ import annotations

import pytest

from ledger.client import LedgerClient
from ledger.queries import (
    has_prior_compliant_verdict,
    project_decision_status,
)
from ledger.schema import init_schema, migrate


async def _fresh_client() -> LedgerClient:
    c = LedgerClient(url="memory://", ns="pds_test", db="ledger_test")
    await c.connect()
    await init_schema(c)
    await migrate(c, allow_destructive=True)
    return c


async def _seed_decision(client: LedgerClient, description: str = "test decision") -> str:
    # canonical_id has a UNIQUE index — derive a stable unique value from the
    # description so multiple decisions in one test don't collide.
    import hashlib

    canonical = hashlib.sha256(description.encode()).hexdigest()[:16]
    rows = await client.query(
        "CREATE decision SET description = $d, canonical_id = $c, source_type = 'manual'",
        {"d": description, "c": canonical},
    )
    return str(rows[0]["id"])


async def _seed_region(
    client: LedgerClient,
    content_hash: str = "",
) -> str:
    rows = await client.query(
        "CREATE code_region SET file_path = 'src/foo.py', symbol_name = 'do_thing', "
        "start_line = 1, end_line = 10, content_hash = $h",
        {"h": content_hash},
    )
    return str(rows[0]["id"])


async def _bind(client: LedgerClient, decision_id: str, region_id: str) -> None:
    await client.query(
        f"RELATE {decision_id}->binds_to->{region_id} "
        "SET created_at = time::now(), confidence = 0.9",
    )


async def _seed_verdict(
    client: LedgerClient,
    decision_id: str,
    region_id: str,
    content_hash: str,
    verdict: str = "compliant",
) -> None:
    await client.query(
        "CREATE compliance_check SET decision_id = $d, region_id = $r, "
        "content_hash = $h, verdict = $v, confidence = 'high', explanation = '', "
        "phase = 'ingest'",
        {"d": decision_id, "r": region_id, "h": content_hash, "v": verdict},
    )


# ── has_prior_compliant_verdict ──────────────────────────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_has_prior_returns_false_when_no_verdict_ever():
    c = await _fresh_client()
    try:
        assert await has_prior_compliant_verdict(c, "decision:a", "code_region:r") is False
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_has_prior_returns_true_when_compliant_exists():
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        region = await _seed_region(c, content_hash="h1")
        await _seed_verdict(c, decision, region, "h1", "compliant")
        assert await has_prior_compliant_verdict(c, decision, region) is True
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_has_prior_returns_false_when_only_drifted_verdicts():
    """Only compliant verdicts prove the code WAS once verified.

    A drifted verdict means the code was previously checked and found
    non-compliant — that's not the "was verified, now changed" case.
    """
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        region = await _seed_region(c, content_hash="h1")
        await _seed_verdict(c, decision, region, "h1", "drifted")
        assert await has_prior_compliant_verdict(c, decision, region) is False
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_has_prior_scoped_to_decision_region_pair():
    """A compliant verdict on a DIFFERENT (decision, region) must not leak."""
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        other_decision = await _seed_decision(c, description="other")
        region = await _seed_region(c, content_hash="h1")
        await _seed_verdict(c, other_decision, region, "h1", "compliant")
        assert await has_prior_compliant_verdict(c, decision, region) is False
    finally:
        await c.close()


# ── project_decision_status — the core regression ───────────────────


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_projected_status_drifted_when_code_changed_after_verification():
    """The Jacob-blocker regression test.

    Flow: decision bound, region hashed, compliance verified for that hash,
    then code changes (region.content_hash updated to new hash). Without
    has_prior_compliant_verdict check, this silently returned 'pending' and
    the session-start banner never surfaced the drift.
    """
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        region = await _seed_region(c, content_hash="h_new")  # current hash
        await _bind(c, decision, region)
        # Prior verification was for the old hash, not h_new
        await _seed_verdict(c, decision, region, "h_old", "compliant")

        status = await project_decision_status(c, decision)
        assert status == "drifted", (
            "Decisions whose code has changed since verification must flip to "
            "drifted, not stay at pending. This is the regression from v0.5.0."
        )
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_projected_status_pending_when_first_time_bind():
    """First-time bind — stored_hash set but no verdict ever existed.

    This must stay pending, not drift to 'drifted'. The cache is empty
    because nobody has verified this region yet, not because code changed.
    """
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        region = await _seed_region(c, content_hash="h1")
        await _bind(c, decision, region)
        # No verdict seeded for any hash

        status = await project_decision_status(c, decision)
        assert status == "pending"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_projected_status_reflected_when_current_hash_compliant():
    """Happy path: verdict exists for current content_hash → reflected."""
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        region = await _seed_region(c, content_hash="h1")
        await _bind(c, decision, region)
        await _seed_verdict(c, decision, region, "h1", "compliant")
        # signoff is None by default → hero case returns 'pending'
        # unless signoff is ratified. Set it to confirm reflected.
        await c.query(
            f"UPDATE {decision} SET signoff = {{ state: 'ratified', signer: 'owner@co', ratified_at: time::now() }}",
        )
        status = await project_decision_status(c, decision)
        assert status == "reflected"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_projected_status_ungrounded_when_no_bindings():
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        # No bindings created

        status = await project_decision_status(c, decision)
        assert status == "ungrounded"
    finally:
        await c.close()


@pytest.mark.phase2
@pytest.mark.asyncio
async def test_projected_status_drifted_wins_over_pending():
    """Multi-region decision: one region drifted + one pending → drifted wins."""
    c = await _fresh_client()
    try:
        decision = await _seed_decision(c)
        region_drifted = await _seed_region(c, content_hash="h_new")
        region_pending = await _seed_region(c, content_hash="h_first")
        await _bind(c, decision, region_drifted)
        await _bind(c, decision, region_pending)
        # Region 1: prior verdict on old hash → current is drift
        await _seed_verdict(c, decision, region_drifted, "h_old", "compliant")
        # Region 2: no verdict anywhere → pending

        status = await project_decision_status(c, decision)
        assert status == "drifted"
    finally:
        await c.close()
