"""Phase C of #278 Phase 2 — bicameral.remove_source handler tests.

Pins:
  1. Reason is required.
  2. confirm=False is a pure dry-run — returns the plan, no mutation.
  3. confirm=True performs the cascade: per-decision signoff UPDATE +
     hard-delete of yields edges + hard-delete of the input_span row.
  4. The single source_removed.completed event payload carries the FULL
     pre-deletion span content (recoverability anchor).
  5. Idempotent on missing span (no exception; structured response).
  6. Cascaded decisions carry removed_by_source back-pointer to the span_id.

#357 backfill — the remove_source cluster's four solitary-trap rows
(``decision_exists``, ``get_decisions_for_span``, ``input_span_exists``,
``get_input_span_row``). Replaces the prior ``_FakeClient`` that parsed
SurrealQL with string matching — a #309-class trap that masked any
parse error or contract drift in the real SurrealQL. Tests now seed a
real ``SurrealDBLedgerAdapter`` over ``memory://`` and exercise the
handler against the real client, per CLAUDE.md's sociable-testing rule
for handler + ledger UX paths.

The only retained narrow seam is the in-memory ``_FakeWriter`` — the
event writer is an emitter contract, not a ledger query, and CLAUDE.md
rule 5 explicitly permits this kind of narrow boundary mock.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.queries import (
    relate_yields,
    upsert_decision,
    upsert_input_span,
)
from ledger.schema import init_schema, migrate

pytestmark = pytest.mark.asyncio


_NS_COUNTER = 0


async def _fresh_adapter() -> tuple[SurrealDBLedgerAdapter, LedgerClient]:
    """Build a fresh ``memory://`` SurrealDB adapter with schema migrated.

    Mirrors the ``_fresh_adapter`` pattern in
    ``tests/test_codegenome_phase4_link_commit.py`` and
    ``tests/test_codegenome_continuity_service.py`` so every test gets a
    private namespace — no cross-test row leakage.
    """
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"remove_source_{_NS_COUNTER}", db="ledger_test")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    return adapter, client


class _FakeWriter:
    """Narrow-seam event collector.

    The event writer is a side-effect emitter, not a ledger query — it
    is the canonical example of CLAUDE.md rule 5 ("narrow seams are
    fine when the alternative is impossible or fragile"). Tests need
    to assert what event payload the handler emits; spinning up the
    real event subsystem (SQLite journal, archive, replayer) would
    test the emitter, not the handler under test.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


def _make_ctx(
    adapter: SurrealDBLedgerAdapter,
    *,
    writer: _FakeWriter | None = None,
    session_id: str = "sess-1",
    authoritative_sha: str = "deadbeef",
) -> SimpleNamespace:
    """``SimpleNamespace`` per CLAUDE.md — missing attrs raise honestly."""
    if writer is not None:
        # Handler reads via ``getattr(ledger, "_writer", None)`` — see
        # handlers/remove_source.py. ``_writer`` is not a declared field
        # on the adapter; team-mode wiring injects it dynamically in
        # production, so the test mirrors that pattern.
        adapter._writer = writer  # type: ignore[attr-defined]
    return SimpleNamespace(
        ledger=adapter,
        session_id=session_id,
        authoritative_sha=authoritative_sha,
    )


async def _seed_span_with_decisions(
    client: LedgerClient,
    *,
    span_text: str = "verbatim transcript excerpt",
    source_ref: str = "meeting-001",
    source_type: str = "transcript",
    speakers: tuple[str, ...] = ("Jin",),
    meeting_date: str = "2026-05-14",
    decision_payloads: tuple[dict, ...] = (),
) -> tuple[str, list[str]]:
    """Seed one input_span + N decisions linked by yields edges.

    Returns ``(span_id, [decision_id, ...])`` using the real ids the
    ledger assigned. Tests must use these ids — there is no
    ``decision:d1`` literal anymore.
    """
    span_id = await upsert_input_span(
        client,
        text=span_text,
        source_type=source_type,
        source_ref=source_ref,
        speakers=list(speakers),
        meeting_date=meeting_date,
    )
    decision_ids: list[str] = []
    for i, payload in enumerate(decision_payloads):
        did = await upsert_decision(
            client,
            description=payload.get("description", f"decision {i}"),
            source_type=source_type,
            source_ref=f"{source_ref}#dec{i}",  # distinct canonical_id per decision
            signoff=payload.get("signoff"),
        )
        await relate_yields(client, span_id, did)
        decision_ids.append(did)
    return span_id, decision_ids


# ── Tests ───────────────────────────────────────────────────────────


async def test_remove_source_rejects_empty_reason() -> None:
    """Discipline #1 — reason is a hard audit-trail obligation."""
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    try:
        span_id, _ = await _seed_span_with_decisions(
            client,
            decision_payloads=({"description": "d"},),
        )
        ctx = _make_ctx(adapter)
        with pytest.raises(ValueError, match="non-empty 'reason'"):
            await handle_remove_source(ctx, span_id=span_id, signer="x@y", reason="", confirm=True)
    finally:
        await client.close()


async def test_remove_source_dry_run_returns_plan_with_cascaded_decision_ids() -> None:
    """Discipline #2 — confirm=False is a pure dry-run."""
    from contracts import RemoveSourcePlan
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    try:
        span_id, decision_ids = await _seed_span_with_decisions(
            client,
            decision_payloads=(
                {"description": "d1", "signoff": {"state": "ratified", "signer": "old"}},
                {"description": "d2", "signoff": {"state": "proposed"}},
                {"description": "d3"},  # never signed off → signoff=None
            ),
        )
        ctx = _make_ctx(adapter)

        plan = await handle_remove_source(
            ctx, span_id=span_id, signer="x@y", reason="bad ingest", confirm=False
        )

        assert isinstance(plan, RemoveSourcePlan)
        assert plan.span_id == span_id
        assert plan.span_existed is True
        assert plan.input_span_content["text"] == "verbatim transcript excerpt"
        assert plan.input_span_content["source_ref"] == "meeting-001"
        assert set(plan.decision_ids) == set(decision_ids)

        # CRITICAL: dry-run must not mutate. Observable check — query the
        # real ledger to confirm the span row is still there and the
        # ratified decision still says "ratified".
        span_rows = await client.query(f"SELECT id FROM {span_id} LIMIT 1")
        assert span_rows, "dry-run must not delete the span row"
        d1_rows = await client.query(f"SELECT signoff FROM {decision_ids[0]} LIMIT 1")
        assert d1_rows[0]["signoff"]["state"] == "ratified"
    finally:
        await client.close()


async def test_remove_source_dry_run_idempotent_on_missing_span() -> None:
    """Discipline #5 — missing span returns structured response, not error."""
    from contracts import RemoveSourcePlan
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    try:
        ctx = _make_ctx(adapter)
        plan = await handle_remove_source(
            ctx, span_id="input_span:missing", signer="x@y", reason="ghost", confirm=False
        )
        assert isinstance(plan, RemoveSourcePlan)
        assert plan.span_existed is False
        assert plan.decision_ids == []
    finally:
        await client.close()


async def test_remove_source_confirm_true_hard_deletes_span_and_soft_deletes_decisions() -> None:
    """Discipline #3 + #6 — cascade soft-deletes decisions with back-pointer,
    hard-deletes the span row and its outgoing yields edges."""
    from contracts import RemoveSourceResponse
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    try:
        span_id, decision_ids = await _seed_span_with_decisions(
            client,
            decision_payloads=(
                {"description": "d1", "signoff": {"state": "ratified", "signer": "old"}},
                {"description": "d2", "signoff": {"state": "proposed"}},
                {"description": "d3"},  # signoff=None
            ),
        )
        ctx = _make_ctx(adapter)

        resp = await handle_remove_source(
            ctx,
            span_id=span_id,
            signer="kim@x",
            reason="duplicate transcript",
            confirm=True,
        )

        assert isinstance(resp, RemoveSourceResponse)
        assert resp.span_existed is True
        assert set(resp.cascaded_decision_ids) == set(decision_ids)

        # input_span row is hard-deleted
        remaining = await client.query(f"SELECT id FROM {span_id} LIMIT 1")
        assert remaining == [] or remaining is None

        # yields edges from this span are gone
        yields_rows = await client.query(
            "SELECT id FROM yields WHERE in = $s",
            {"s": span_id},
        )
        assert yields_rows == [] or yields_rows is None

        # Every cascaded decision has signoff.state="removed" + back-pointer.
        # SurrealDB strips None-valued keys from FLEXIBLE objects on
        # round-trip — the previously-unsigned decision's previous_state
        # therefore comes back as a missing key, not a present null. Use
        # ``.get()`` so the test asserts what the agent observes.
        prev_states = {
            decision_ids[0]: "ratified",
            decision_ids[1]: "proposed",
            decision_ids[2]: None,
        }
        for did in decision_ids:
            rows = await client.query(f"SELECT signoff FROM {did} LIMIT 1")
            signoff = rows[0]["signoff"]
            assert signoff["state"] == "removed"
            assert signoff["removed_by_source"] == span_id
            assert signoff["reason"] == "duplicate transcript"
            assert signoff["signer"] == "kim@x"
            assert signoff["removed_at"]
            assert signoff.get("previous_state") == prev_states[did]
    finally:
        await client.close()


async def test_remove_source_confirm_emits_single_event_with_full_span_content() -> None:
    """Discipline #4 — the source_removed.completed event payload carries the
    FULL pre-deletion span content so the action is recoverable from the
    event log."""
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    writer = _FakeWriter()
    try:
        span_id, decision_ids = await _seed_span_with_decisions(
            client,
            decision_payloads=(
                {"description": "d1"},
                {"description": "d2"},
                {"description": "d3"},
            ),
        )
        ctx = _make_ctx(adapter, writer=writer)

        resp = await handle_remove_source(
            ctx, span_id=span_id, signer="kim@x", reason="bad", confirm=True
        )

        from contracts import RemoveSourceResponse

        assert isinstance(resp, RemoveSourceResponse)
        assert resp.event_logged is True
        assert len(writer.events) == 1  # single event for the entire cascade
        event_type, payload = writer.events[0]
        assert event_type == "source_removed.completed"
        assert payload["span_id"] == span_id
        assert payload["input_span_content"]["text"] == "verbatim transcript excerpt"
        assert payload["input_span_content"]["source_ref"] == "meeting-001"
        assert payload["input_span_content"]["source_type"] == "transcript"
        assert set(payload["cascaded_decision_ids"]) == set(decision_ids)
        assert payload["signer"] == "kim@x"
        assert payload["reason"] == "bad"
        assert payload["removed_at"]
    finally:
        await client.close()


async def test_remove_source_dry_run_emits_no_event() -> None:
    """A dry-run must not write to the event log."""
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    writer = _FakeWriter()
    try:
        span_id, _ = await _seed_span_with_decisions(
            client,
            decision_payloads=({"description": "d1"},),
        )
        ctx = _make_ctx(adapter, writer=writer)
        await handle_remove_source(
            ctx, span_id=span_id, signer="x@y", reason="check", confirm=False
        )
        assert writer.events == []
    finally:
        await client.close()


async def test_remove_source_confirm_idempotent_on_missing_span() -> None:
    """confirm=True on a non-existent span returns structured response, not
    an exception. Pins idempotency Discipline #5."""
    from contracts import RemoveSourceResponse
    from handlers.remove_source import handle_remove_source

    adapter, client = await _fresh_adapter()
    writer = _FakeWriter()
    try:
        ctx = _make_ctx(adapter, writer=writer)
        resp = await handle_remove_source(
            ctx,
            span_id="input_span:missing",
            signer="x@y",
            reason="ghost cleanup",
            confirm=True,
        )

        assert isinstance(resp, RemoveSourceResponse)
        assert resp.span_existed is False
        assert resp.cascaded_decision_ids == []
        assert resp.event_logged is False
        # No event emitted on the no-op path even though writer was attached
        assert writer.events == []
    finally:
        await client.close()


async def test_remove_source_skips_already_removed_decisions_in_cascade() -> None:
    """Per-decision idempotency: a decision previously soft-deleted (by an
    earlier remove_decision call) is reported in cascaded_decision_ids but
    its signoff row is not re-written."""
    from handlers.remove_source import handle_remove_source

    pre_existing_removed = {
        "state": "removed",
        "signer": "earlier@x",
        "reason": "earlier removal",
        "previous_state": "ratified",
    }

    adapter, client = await _fresh_adapter()
    try:
        span_id, decision_ids = await _seed_span_with_decisions(
            client,
            decision_payloads=(
                {"description": "d1", "signoff": pre_existing_removed},
                {"description": "d2", "signoff": {"state": "proposed"}},
            ),
        )
        d1, d2 = decision_ids
        ctx = _make_ctx(adapter)

        resp = await handle_remove_source(
            ctx, span_id=span_id, signer="kim@x", reason="cascade", confirm=True
        )

        from contracts import RemoveSourceResponse

        assert isinstance(resp, RemoveSourceResponse)
        assert set(resp.cascaded_decision_ids) == {d1, d2}

        # d1's signoff was NOT overwritten — it kept its earlier removal record.
        # Compare on the load-bearing keys to avoid coupling to insertion-order
        # quirks of FLEXIBLE objects.
        rows = await client.query(f"SELECT signoff FROM {d1} LIMIT 1")
        d1_signoff = rows[0]["signoff"]
        assert d1_signoff["state"] == "removed"
        assert d1_signoff["signer"] == "earlier@x"
        assert d1_signoff["reason"] == "earlier removal"
        assert d1_signoff["previous_state"] == "ratified"
        # No removed_by_source key — that's what the new cascade would have added.
        assert "removed_by_source" not in d1_signoff

        # d2 was newly removed by the cascade
        rows = await client.query(f"SELECT signoff FROM {d2} LIMIT 1")
        d2_signoff = rows[0]["signoff"]
        assert d2_signoff["state"] == "removed"
        assert d2_signoff["reason"] == "cascade"
        assert d2_signoff["removed_by_source"] == span_id
        assert d2_signoff["previous_state"] == "proposed"
    finally:
        await client.close()


async def test_get_decisions_for_span_returns_yield_traversal_ids() -> None:
    """Sociable unit test for the ``get_decisions_for_span`` ledger query.

    Replaces the old SQL-string-matching fake. Exercises the real
    ``<-yields<-input_span CONTAINS ...`` graph traversal against a
    seeded ledger — a parse error or contract drift now fails honestly
    here instead of silently green-lighting drift in production."""
    from ledger.queries import get_decisions_for_span

    adapter, client = await _fresh_adapter()
    try:
        span_id, decision_ids = await _seed_span_with_decisions(
            client,
            decision_payloads=(
                {"description": "a"},
                {"description": "b"},
            ),
        )
        # Seed a second span with its own decision so we can prove the
        # traversal filters by span_id, not "all decisions".
        other_span_id = await upsert_input_span(
            client,
            text="other excerpt",
            source_type="manual",
            source_ref="other-001",
        )
        other_decision_id = await upsert_decision(
            client,
            description="c",
            source_type="manual",
            source_ref="other-001#dec0",
        )
        await relate_yields(client, other_span_id, other_decision_id)

        result = await get_decisions_for_span(client, span_id)
        assert set(result) == set(decision_ids)
        assert other_decision_id not in result
    finally:
        await client.close()
