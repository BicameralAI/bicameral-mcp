"""Sociable tests for bicameral.remove_decision (hard-delete contract).

Per decision:i4wafafzowm3ai5eyhgs, remove_decision physically deletes
the decision row + all references; soft-delete / tombstone is no longer
a concept. The event journal carries the full pre-deletion snapshot as
the "soft audit trail".

Tests run against a real SurrealDBLedgerAdapter over ``memory://`` per
``pilot/mcp/CLAUDE.md`` sociable-testing policy — no MagicMock for
``ctx`` or ``ledger``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ledger.adapter import SurrealDBLedgerAdapter
from ledger.client import LedgerClient
from ledger.schema import init_schema, migrate

pytestmark = [pytest.mark.asyncio, pytest.mark.phase2]


_NS_COUNTER = 0


async def _fresh_adapter(suffix: str) -> SurrealDBLedgerAdapter:
    """Real adapter over memory://, fresh schema per test.

    Pattern mirrors ``tests/test_sync_middleware.py::_make_real_adapter`` —
    each call gets a unique namespace so rows from one test never leak
    into another. We assemble the adapter manually because
    ``SurrealDBLedgerAdapter.connect()`` doesn't accept ns/db kwargs and
    each test needs an isolated DB.
    """
    global _NS_COUNTER
    _NS_COUNTER += 1
    client = LedgerClient(url="memory://", ns=f"remove_dec_{_NS_COUNTER}", db=f"rd_{suffix}")
    await client.connect()
    await init_schema(client)
    await migrate(client, allow_destructive=True)
    adapter = SurrealDBLedgerAdapter(url="memory://")
    adapter._client = client
    adapter._connected = True
    adapter._pii_archive = None
    return adapter


_SEED_COUNTER = 0


def _next_canonical(prefix: str = "rd") -> str:
    """Monotonic canonical_id per seed.

    ``decision.canonical_id`` carries a UNIQUE index (schema.py
    ``idx_decision_canonical``); the field default is ``''`` so a second
    seed in the same DB collides. The pattern matches the one in
    ``tests/test_sync_middleware.py``.
    """
    global _SEED_COUNTER
    _SEED_COUNTER += 1
    return f"{prefix}-{_SEED_COUNTER}"


async def _seed_decision(
    adapter: SurrealDBLedgerAdapter,
    *,
    description: str,
    signoff_state: str = "ratified",
    parent_id: str | None = None,
) -> str:
    """Create one decision row directly via the client. Returns its id."""
    canonical = _next_canonical()
    sql = (
        "CREATE decision SET description=$d, source_type='test', "
        "source_ref='unit-test', status='pending', "
        "canonical_id=$cid, signoff=$so"
    )
    params = {
        "d": description,
        "cid": canonical,
        "so": {"state": signoff_state, "signer": "seed@test"},
    }
    if parent_id is not None:
        sql += ", parent_decision_id=$p"
        params["p"] = parent_id
    rows = await adapter._client.query(sql, params)
    return str(rows[0]["id"])


def _ctx(adapter: SurrealDBLedgerAdapter, *, writer=None) -> SimpleNamespace:
    """Build a SimpleNamespace ctx — fails honestly on missing fields
    (vs MagicMock which would silently invent them)."""
    ledger = adapter
    if writer is not None:
        # Attach writer so the handler's team-mode branch fires.
        ledger._writer = writer
    return SimpleNamespace(
        ledger=ledger,
        repo_path="/tmp/test-repo",
        session_id="sess-test",
        authoritative_sha="testsha0",
    )


class _CapturingWriter:
    """Captures events the handler emits when wired as ``adapter._writer``."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, event_type: str, payload: dict) -> None:
        self.events.append((event_type, payload))


# ── Contract: empty reason rejected ─────────────────────────────────────


async def test_rejects_empty_reason() -> None:
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("empty_reason")
    try:
        did = await _seed_decision(adapter, description="some decision")
        with pytest.raises(ValueError, match="non-empty 'reason'"):
            await handle_remove_decision(_ctx(adapter), decision_id=did, signer="x@y", reason="")
    finally:
        await adapter._client.close()


async def test_rejects_whitespace_only_reason() -> None:
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("ws_reason")
    try:
        did = await _seed_decision(adapter, description="some decision")
        with pytest.raises(ValueError, match="non-empty 'reason'"):
            await handle_remove_decision(
                _ctx(adapter), decision_id=did, signer="x@y", reason="   \t\n"
            )
    finally:
        await adapter._client.close()


# ── Contract: idempotent on missing decision ─────────────────────────────


async def test_missing_decision_is_idempotent_no_op() -> None:
    """Per v0.15.x hard-delete contract: a missing decision_id returns
    was_new=False and event_logged=False without raising. The matching
    event in the journal (if any) is the canonical record of any prior
    removal — the handler does not try to recreate it."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("missing")
    try:
        resp = await handle_remove_decision(
            _ctx(adapter),
            decision_id="decision:does_not_exist",
            signer="x@y",
            reason="probe",
        )
        assert resp.was_new is False
        assert resp.event_logged is False
        assert resp.removed_at is None
        assert resp.previous_state is None
        assert resp.reason == "probe"
    finally:
        await adapter._client.close()


# ── Contract: row + edges + compliance_check are physically gone ─────────


async def test_hard_delete_removes_row_edges_and_compliance_cache() -> None:
    """The decision row, its binds_to/yields edges, and any
    compliance_check rows keyed on it must all be physically gone after
    a successful call."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("hard")
    try:
        # Seed decision + 1 yields edge + 1 binds_to edge + 1
        # compliance_check row so we can confirm each is cleaned up.
        did = await _seed_decision(adapter, description="will be deleted")
        span_rows = await adapter._client.query(
            "CREATE input_span SET text='probe', source_type='test', source_ref='r'"
        )
        sid = str(span_rows[0]["id"])
        region_rows = await adapter._client.query(
            "CREATE code_region SET file_path='probe.py', symbol_name='probe', "
            "start_line=1, end_line=2"
        )
        rid = str(region_rows[0]["id"])
        await adapter._client.query(f"RELATE {sid}->yields->{did}")
        # binds_to requires confidence (TYPE float) on the edge.
        await adapter._client.query(f"RELATE {did}->binds_to->{rid} SET confidence = 1.0")
        await adapter._client.query(
            "CREATE compliance_check SET decision_id=$d, region_id=$r, "
            "content_hash='hash1', verdict='compliant', confidence='high', "
            "explanation='seed', phase='ingest'",
            {"d": did, "r": rid},
        )

        # Pre-condition sanity
        pre_d = await adapter._client.query(f"SELECT id FROM {did}")
        pre_yields = await adapter._client.query(f"SELECT id FROM yields WHERE out = {did}")
        pre_binds = await adapter._client.query(f"SELECT id FROM binds_to WHERE in = {did}")
        pre_cc = await adapter._client.query(
            "SELECT id FROM compliance_check WHERE decision_id = $d", {"d": did}
        )
        assert pre_d and pre_yields and pre_binds and pre_cc

        resp = await handle_remove_decision(
            _ctx(adapter),
            decision_id=did,
            signer="kim@test",
            reason="Duplicate of decision:abc — keeping the earlier copy",
        )

        # Response shape
        assert resp.decision_id == did
        assert resp.was_new is True
        assert resp.removed_at  # ISO timestamp populated
        assert resp.previous_state == "ratified"
        assert resp.reason.startswith("Duplicate of")

        # Row + every reference must be gone.
        post_d = await adapter._client.query(f"SELECT id FROM {did}")
        post_yields = await adapter._client.query(f"SELECT id FROM yields WHERE out = {did}")
        post_binds = await adapter._client.query(f"SELECT id FROM binds_to WHERE in = {did}")
        post_cc = await adapter._client.query(
            "SELECT id FROM compliance_check WHERE decision_id = $d", {"d": did}
        )
        assert post_d == []
        assert post_yields == []
        assert post_binds == []
        assert post_cc == []

        # The input_span + code_region rows must NOT be touched — they
        # could be referenced by other decisions; orphan removal is a
        # separate concern.
        post_span = await adapter._client.query(f"SELECT id FROM {sid}")
        post_region = await adapter._client.query(f"SELECT id FROM {rid}")
        assert post_span and post_region
    finally:
        await adapter._client.close()


# ── Contract: children orphaned cleanly ──────────────────────────────────


async def test_child_decisions_get_orphaned_to_root() -> None:
    """When a parent decision is removed, children whose
    ``parent_decision_id`` pointed at it must have that field set to
    NONE — they become root-level decisions, not dangling pointers."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("orphan")
    try:
        parent_id = await _seed_decision(adapter, description="parent decision")
        child_id = await _seed_decision(adapter, description="child decision", parent_id=parent_id)

        await handle_remove_decision(
            _ctx(adapter), decision_id=parent_id, signer="x@y", reason="cleanup"
        )

        # Parent gone, child still present but with NONE parent_decision_id.
        post_child = await adapter._client.query(
            f"SELECT type::string(id) AS id, parent_decision_id FROM {child_id}"
        )
        assert post_child, "child row should still exist"
        assert post_child[0].get("parent_decision_id") in (None, "")
    finally:
        await adapter._client.close()


# ── Contract: idempotent second-call returns was_new=False ───────────────


async def test_second_call_returns_was_new_false() -> None:
    """After a successful hard-delete, a second call with the same id
    returns ``was_new=False`` (the row is gone) — the matching event in
    the journal is the canonical record of the original removal."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("idempotent")
    try:
        did = await _seed_decision(adapter, description="will be deleted")

        first = await handle_remove_decision(
            _ctx(adapter), decision_id=did, signer="x@y", reason="first call"
        )
        assert first.was_new is True

        second = await handle_remove_decision(
            _ctx(adapter), decision_id=did, signer="x@y", reason="second call"
        )
        assert second.was_new is False
        assert second.event_logged is False
        assert second.removed_at is None
    finally:
        await adapter._client.close()


# ── Contract: event emitted in team mode with full snapshot ──────────────


async def test_emits_event_with_full_snapshot_in_team_mode() -> None:
    """When the adapter exposes ``_writer`` (team mode), the handler
    emits one ``decision_removed.completed`` event whose payload carries
    the FULL pre-deletion snapshot — recoverable audit trail."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("event_team")
    try:
        did = await _seed_decision(adapter, description="will be deleted", signoff_state="proposed")

        writer = _CapturingWriter()
        await handle_remove_decision(
            _ctx(adapter, writer=writer),
            decision_id=did,
            signer="kim@test",
            reason="Duplicate of decision:abc — keeping the earlier copy",
        )

        assert len(writer.events) == 1
        event_type, payload = writer.events[0]
        assert event_type == "decision_removed.completed"
        assert payload["decision_id"] == did
        assert payload["signer"] == "kim@test"
        assert payload["reason"].startswith("Duplicate of")
        assert payload["previous_state"] == "proposed"
        assert payload["removed_at"]
        # Full pre-deletion snapshot present
        snapshot = payload["snapshot"]
        assert snapshot["description"] == "will be deleted"
        assert snapshot["source_type"] == "test"
        assert snapshot["source_ref"] == "unit-test"
        assert isinstance(snapshot["signoff"], dict)
        assert snapshot["signoff"]["state"] == "proposed"
    finally:
        await adapter._client.close()


async def test_skips_event_emission_in_local_mode() -> None:
    """No ``_writer`` attached → no event emitted, no exception raised."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("event_local")
    try:
        did = await _seed_decision(adapter, description="local mode probe")
        # Ensure the adapter genuinely has no writer attached.
        assert not hasattr(adapter, "_writer") or adapter._writer is None
        ctx = SimpleNamespace(
            ledger=adapter,
            repo_path="/tmp/test-repo",
            session_id="sess-test",
            authoritative_sha="testsha0",
        )

        resp = await handle_remove_decision(ctx, decision_id=did, signer="x@y", reason="cleanup")
        assert resp.was_new is True
        assert resp.event_logged is False
    finally:
        await adapter._client.close()


async def test_idempotent_call_does_not_emit_second_event() -> None:
    """Second call on an already-deleted decision must NOT emit a second
    event, even when team mode is active."""
    from handlers.remove_decision import handle_remove_decision

    adapter = await _fresh_adapter("idem_event")
    try:
        did = await _seed_decision(adapter, description="probe")
        writer = _CapturingWriter()

        await handle_remove_decision(
            _ctx(adapter, writer=writer), decision_id=did, signer="x", reason="first"
        )
        await handle_remove_decision(
            _ctx(adapter, writer=writer), decision_id=did, signer="x", reason="second"
        )

        assert len(writer.events) == 1, f"expected exactly one event, got {len(writer.events)}"
    finally:
        await adapter._client.close()
