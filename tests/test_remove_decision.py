"""Phase B of #278 Phase 2 — bicameral.remove_decision handler tests.

Pins:
  1. Reason is required (empty/whitespace → ValueError).
  2. Unknown decision_id → ValueError matching ratify.py's "No decision row" shape.
  3. The handler writes signoff.state="removed" + reason + signer + removed_at
     + previous_state, and re-projects decision status.
  4. Idempotent: second call returns was_new=False, does not emit a second
     event.
  5. Emits decision_removed.completed event when adapter has a _writer
     (team mode); skips emission in local-only mode without raising.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

# Marker mirrors the project's existing phase2-needs-surrealdb convention.
PHASE2 = pytest.mark.phase2


class _FakeClient:
    """Minimal stand-in for the SurrealDB client used by handle_remove_decision.

    Records the queries the handler issues so the test can assert on the SQL
    + parameter shape without spinning up a real SurrealDB. Backed by an
    in-memory dict keyed by decision_id.
    """

    def __init__(self, decisions: dict[str, dict] | None = None) -> None:
        self._rows = decisions or {}
        self.queries: list[tuple[str, dict | None]] = []

    async def query(self, sql: str, params: dict | None = None):
        self.queries.append((sql, params))
        sql_l = sql.lower()
        if "select signoff from" in sql_l:
            # SELECT signoff FROM decision:abc LIMIT 1
            did = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            row = self._rows.get(did, {})
            return [{"signoff": row.get("signoff")}]
        if "update " in sql_l and "set signoff" in sql_l:
            did = sql.split("UPDATE ", 1)[1].split(" SET")[0].strip()
            row = self._rows.setdefault(did, {})
            row["signoff"] = params["signoff"]
            return [row]
        # Fallback: just return empty
        return []


class _FakeLedger:
    """Adapter stand-in. Implements just the surface handle_remove_decision touches."""

    def __init__(self, decisions: dict[str, dict], writer=None) -> None:
        self._inner = self  # handler does getattr(ledger, "_inner", ledger)
        self._client = _FakeClient(decisions)
        if writer is not None:
            self._writer = writer

    async def connect(self) -> None:
        return None


class _FakeWriter:
    """Captures events the handler emits in team mode."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, event_type: str, payload: dict):
        self.events.append((event_type, payload))


class _FakeCtx:
    def __init__(self, ledger, session_id: str = "sess-1", sha: str = "deadbeef") -> None:
        self.ledger = ledger
        self.session_id = session_id
        self.authoritative_sha = sha


# Patch ledger.queries functions to skip real DB work for these handler-shape tests.
@pytest.fixture(autouse=True)
def _stub_queries(monkeypatch):
    async def _decision_exists(client, did):
        rows = await client.query(f"SELECT id FROM {did} LIMIT 1")
        return did in client._rows

    async def _project(client, did):
        return "ungrounded"

    async def _update(client, did, status):
        return None

    monkeypatch.setattr("handlers.remove_decision.decision_exists", _decision_exists)
    monkeypatch.setattr(
        "handlers.remove_decision.project_decision_status", _project
    )
    monkeypatch.setattr(
        "handlers.remove_decision.update_decision_status", _update
    )


async def test_remove_decision_rejects_empty_reason() -> None:
    from handlers.remove_decision import handle_remove_decision

    ledger = _FakeLedger({"decision:abc": {"signoff": {"state": "ratified"}}})
    ctx = _FakeCtx(ledger)

    with pytest.raises(ValueError, match="non-empty 'reason'"):
        await handle_remove_decision(
            ctx, decision_id="decision:abc", signer="x@y", reason=""
        )


async def test_remove_decision_rejects_whitespace_only_reason() -> None:
    from handlers.remove_decision import handle_remove_decision

    ledger = _FakeLedger({"decision:abc": {"signoff": {"state": "ratified"}}})
    ctx = _FakeCtx(ledger)

    with pytest.raises(ValueError, match="non-empty 'reason'"):
        await handle_remove_decision(
            ctx, decision_id="decision:abc", signer="x@y", reason="   \t\n"
        )


async def test_remove_decision_rejects_unknown_decision_id() -> None:
    from handlers.remove_decision import handle_remove_decision

    ledger = _FakeLedger({})  # empty store
    ctx = _FakeCtx(ledger)

    with pytest.raises(ValueError, match="No decision row for decision:missing"):
        await handle_remove_decision(
            ctx, decision_id="decision:missing", signer="x@y", reason="not here"
        )


async def test_remove_decision_writes_signoff_state_removed_with_all_fields() -> None:
    from handlers.remove_decision import handle_remove_decision

    ledger = _FakeLedger({"decision:abc": {"signoff": {"state": "ratified", "signer": "old"}}})
    ctx = _FakeCtx(ledger)

    resp = await handle_remove_decision(
        ctx,
        decision_id="decision:abc",
        signer="kim@example.com",
        reason="Duplicate of decision:def — transcript ingested twice",
    )

    assert resp.was_new is True
    assert resp.decision_id == "decision:abc"
    # The new signoff is the one persisted in the fake store
    persisted = ledger._client._rows["decision:abc"]["signoff"]
    assert persisted["state"] == "removed"
    assert persisted["signer"] == "kim@example.com"
    assert persisted["reason"] == "Duplicate of decision:def — transcript ingested twice"
    assert persisted["previous_state"] == "ratified"
    assert persisted["removed_at"]  # non-empty ISO timestamp
    assert persisted["session_id"] == "sess-1"


async def test_remove_decision_is_idempotent_on_already_removed() -> None:
    from handlers.remove_decision import handle_remove_decision

    existing_signoff = {
        "state": "removed",
        "signer": "first@x",
        "reason": "first removal reason",
        "removed_at": "2026-05-13T00:00:00+00:00",
        "previous_state": "ratified",
    }
    ledger = _FakeLedger({"decision:abc": {"signoff": existing_signoff}})
    ctx = _FakeCtx(ledger)

    resp = await handle_remove_decision(
        ctx, decision_id="decision:abc", signer="second@x", reason="second attempt"
    )

    assert resp.was_new is False
    # The original signoff is unchanged — second call did not overwrite
    persisted = ledger._client._rows["decision:abc"]["signoff"]
    assert persisted == existing_signoff
    # The returned signoff matches the existing one
    assert resp.signoff == existing_signoff


async def test_remove_decision_emits_event_in_team_mode() -> None:
    """When the adapter exposes ._writer (team mode), the handler emits
    decision_removed.completed with the new signoff in the payload."""
    from handlers.remove_decision import handle_remove_decision

    writer = _FakeWriter()
    ledger = _FakeLedger(
        {"decision:abc": {"signoff": {"state": "ratified"}}},
        writer=writer,
    )
    ctx = _FakeCtx(ledger)

    await handle_remove_decision(
        ctx, decision_id="decision:abc", signer="kim@x", reason="cleanup"
    )

    assert len(writer.events) == 1
    event_type, payload = writer.events[0]
    assert event_type == "decision_removed.completed"
    assert payload["decision_id"] == "decision:abc"
    assert payload["signoff"]["state"] == "removed"
    assert payload["signoff"]["reason"] == "cleanup"
    assert payload["signoff"]["previous_state"] == "ratified"


async def test_remove_decision_skips_event_in_local_mode() -> None:
    """No _writer attached → no event emitted, no exception."""
    from handlers.remove_decision import handle_remove_decision

    ledger = _FakeLedger({"decision:abc": {"signoff": {"state": "ratified"}}})
    # No writer attached
    assert not hasattr(ledger, "_writer")
    ctx = _FakeCtx(ledger)

    resp = await handle_remove_decision(
        ctx, decision_id="decision:abc", signer="kim@x", reason="cleanup"
    )

    assert resp.was_new is True
    # Idempotency assertion is sufficient — no writer means no event capture path,
    # but the test serves as a contract that the handler doesn't raise when
    # the writer is absent.


async def test_remove_decision_idempotent_does_not_emit_second_event() -> None:
    """Second call on an already-removed decision must NOT emit a second event
    even when team mode is active."""
    from handlers.remove_decision import handle_remove_decision

    writer = _FakeWriter()
    existing_signoff = {"state": "removed", "signer": "first@x", "reason": "first"}
    ledger = _FakeLedger(
        {"decision:abc": {"signoff": existing_signoff}}, writer=writer
    )
    ctx = _FakeCtx(ledger)

    resp = await handle_remove_decision(
        ctx, decision_id="decision:abc", signer="second@x", reason="second"
    )

    assert resp.was_new is False
    assert writer.events == []  # No event emitted on the no-op path
