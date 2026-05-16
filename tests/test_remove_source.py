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
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# Minimal fakes mirroring the test_remove_decision pattern. The fakes record
# every SQL query the handler issues so tests can assert on the
# cascade-mutation shape without spinning up a real SurrealDB.


class _FakeClient:
    def __init__(
        self,
        spans: dict[str, dict] | None = None,
        decisions: dict[str, dict] | None = None,
        edges: list[tuple[str, str]] | None = None,
    ) -> None:
        self._spans = spans or {}
        self._decisions = decisions or {}
        self._edges = list(edges or [])  # (span_id, decision_id) yields edges
        self.queries: list[tuple[str, dict | None]] = []

    async def query(self, sql: str, params: dict | None = None):
        self.queries.append((sql, params))
        sql_l = sql.lower()

        # input_span_exists / get_input_span_row / decision_exists
        if "select id from input_span:" in sql_l and "limit 1" in sql_l:
            sid = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return [{"id": sid}] if sid in self._spans else []
        if sql_l.startswith("select text, source_ref, source_type") and "from input_span:" in sql_l:
            sid = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            row = self._spans.get(sid)
            return [row] if row else []
        if "select id from decision:" in sql_l and "limit 1" in sql_l:
            did = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return [{"id": did}] if did in self._decisions else []

        # get_decisions_for_span
        if "from decision" in sql_l and "<-yields<-input_span contains" in sql_l:
            span_id = sql.split("CONTAINS ", 1)[1].strip()
            matching = [d for (s, d) in self._edges if s == span_id]
            return [{"decision_id": d} for d in matching]

        # SELECT signoff FROM decision:...
        if "select signoff from decision:" in sql_l:
            did = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return [{"signoff": self._decisions.get(did, {}).get("signoff")}]

        # UPDATE decision:... SET signoff = $signoff
        if "update decision:" in sql_l and "set signoff" in sql_l:
            did = sql.split("UPDATE ", 1)[1].split(" SET")[0].strip()
            row = self._decisions.setdefault(did, {})
            row["signoff"] = params["signoff"]
            return [row]

        # DELETE yields WHERE in = <span_id>
        if sql_l.startswith("delete yields where in"):
            span_id = sql.rsplit("=", 1)[1].strip()
            self._edges = [(s, d) for (s, d) in self._edges if s != span_id]
            return []

        # DELETE input_span:... — hard delete the span row
        if sql_l.startswith("delete input_span:"):
            sid = sql.split("DELETE ", 1)[1].strip()
            self._spans.pop(sid, None)
            return []

        return []


class _FakeLedger:
    def __init__(self, client, writer=None) -> None:
        self._inner = self
        self._client = client
        if writer is not None:
            self._writer = writer

    async def connect(self) -> None:
        return None


class _FakeWriter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write(self, event_type: str, payload: dict):
        self.events.append((event_type, payload))


class _FakeCtx:
    def __init__(self, ledger, session_id="sess-1", sha="deadbeef") -> None:
        self.ledger = ledger
        self.session_id = session_id
        self.authoritative_sha = sha


@pytest.fixture(autouse=True)
def _stub_project_queries(monkeypatch):
    """Stub the status-projection helpers so tests don't need the full
    schema. These are exercised by other tests (project_decision_status
    is well-covered elsewhere)."""

    async def _project(client, did):
        return "ungrounded"

    async def _update(client, did, status):
        return None

    monkeypatch.setattr("handlers.remove_source.project_decision_status", _project)
    monkeypatch.setattr("handlers.remove_source.update_decision_status", _update)


def _seed_fixture():
    """One span with 3 decisions linked via yields."""
    span_id = "input_span:span001"
    span_row = {
        "text": "verbatim transcript excerpt",
        "source_ref": "meeting-001",
        "source_type": "transcript",
        "meeting_date": "2026-05-14",
        "speakers": ["Jin"],
        "created_at": "2026-05-14T00:00:00+00:00",
    }
    decisions = {
        "decision:d1": {"signoff": {"state": "ratified", "signer": "old"}},
        "decision:d2": {"signoff": {"state": "proposed"}},
        "decision:d3": {"signoff": None},  # never signed off
    }
    edges = [(span_id, did) for did in decisions]
    return span_id, span_row, decisions, edges


async def test_remove_source_rejects_empty_reason() -> None:
    from handlers.remove_source import handle_remove_source

    span_id, span_row, decisions, edges = _seed_fixture()
    client = _FakeClient({span_id: span_row}, decisions, edges)
    ctx = _FakeCtx(_FakeLedger(client))

    with pytest.raises(ValueError, match="non-empty 'reason'"):
        await handle_remove_source(ctx, span_id=span_id, signer="x@y", reason="", confirm=True)


async def test_remove_source_dry_run_returns_plan_with_cascaded_decision_ids() -> None:
    from contracts import RemoveSourcePlan
    from handlers.remove_source import handle_remove_source

    span_id, span_row, decisions, edges = _seed_fixture()
    client = _FakeClient({span_id: span_row}, decisions, edges)
    ctx = _FakeCtx(_FakeLedger(client))

    plan = await handle_remove_source(
        ctx, span_id=span_id, signer="x@y", reason="bad ingest", confirm=False
    )

    assert isinstance(plan, RemoveSourcePlan)
    assert plan.span_id == span_id
    assert plan.span_existed is True
    assert plan.input_span_content["text"] == "verbatim transcript excerpt"
    assert plan.input_span_content["source_ref"] == "meeting-001"
    assert set(plan.decision_ids) == {"decision:d1", "decision:d2", "decision:d3"}
    # CRITICAL: dry-run must not mutate
    assert span_id in client._spans
    assert decisions["decision:d1"]["signoff"]["state"] == "ratified"


async def test_remove_source_dry_run_idempotent_on_missing_span() -> None:
    from contracts import RemoveSourcePlan
    from handlers.remove_source import handle_remove_source

    client = _FakeClient({}, {}, [])  # empty store
    ctx = _FakeCtx(_FakeLedger(client))

    plan = await handle_remove_source(
        ctx, span_id="input_span:missing", signer="x@y", reason="ghost", confirm=False
    )
    assert isinstance(plan, RemoveSourcePlan)
    assert plan.span_existed is False
    assert plan.decision_ids == []


async def test_remove_source_confirm_true_hard_deletes_span_and_soft_deletes_decisions() -> None:
    from contracts import RemoveSourceResponse
    from handlers.remove_source import handle_remove_source

    span_id, span_row, decisions, edges = _seed_fixture()
    client = _FakeClient({span_id: span_row}, decisions, edges)
    ctx = _FakeCtx(_FakeLedger(client))

    resp = await handle_remove_source(
        ctx,
        span_id=span_id,
        signer="kim@x",
        reason="duplicate transcript",
        confirm=True,
    )

    assert isinstance(resp, RemoveSourceResponse)
    assert resp.span_existed is True
    assert set(resp.cascaded_decision_ids) == {"decision:d1", "decision:d2", "decision:d3"}
    # input_span row is hard-deleted
    assert span_id not in client._spans
    # yields edges from this span are gone
    assert not any(s == span_id for (s, _d) in client._edges)
    # Every cascaded decision has signoff.state="removed" + back-pointer
    for did in ("decision:d1", "decision:d2", "decision:d3"):
        signoff = client._decisions[did]["signoff"]
        assert signoff["state"] == "removed"
        assert signoff["removed_by_source"] == span_id
        assert signoff["reason"] == "duplicate transcript"
        assert signoff["signer"] == "kim@x"
        assert signoff["removed_at"]
    # previous_state recorded for forensic review
    assert client._decisions["decision:d1"]["signoff"]["previous_state"] == "ratified"
    assert client._decisions["decision:d2"]["signoff"]["previous_state"] == "proposed"
    assert client._decisions["decision:d3"]["signoff"]["previous_state"] is None


async def test_remove_source_confirm_emits_single_event_with_full_span_content() -> None:
    """Discipline #3: the source_removed.completed event payload carries the
    FULL pre-deletion span content so the action is recoverable from the
    event log."""
    from handlers.remove_source import handle_remove_source

    span_id, span_row, decisions, edges = _seed_fixture()
    writer = _FakeWriter()
    client = _FakeClient({span_id: span_row}, decisions, edges)
    ctx = _FakeCtx(_FakeLedger(client, writer=writer))

    resp = await handle_remove_source(
        ctx, span_id=span_id, signer="kim@x", reason="bad", confirm=True
    )

    assert resp.event_logged is True
    assert len(writer.events) == 1  # single event for the entire cascade
    event_type, payload = writer.events[0]
    assert event_type == "source_removed.completed"
    assert payload["span_id"] == span_id
    # Full pre-deletion span content is in the payload
    assert payload["input_span_content"]["text"] == "verbatim transcript excerpt"
    assert payload["input_span_content"]["source_ref"] == "meeting-001"
    assert payload["input_span_content"]["source_type"] == "transcript"
    assert set(payload["cascaded_decision_ids"]) == {
        "decision:d1",
        "decision:d2",
        "decision:d3",
    }
    assert payload["signer"] == "kim@x"
    assert payload["reason"] == "bad"
    assert payload["removed_at"]


async def test_remove_source_dry_run_emits_no_event() -> None:
    from handlers.remove_source import handle_remove_source

    span_id, span_row, decisions, edges = _seed_fixture()
    writer = _FakeWriter()
    client = _FakeClient({span_id: span_row}, decisions, edges)
    ctx = _FakeCtx(_FakeLedger(client, writer=writer))

    await handle_remove_source(ctx, span_id=span_id, signer="x@y", reason="check", confirm=False)
    assert writer.events == []


async def test_remove_source_confirm_idempotent_on_missing_span() -> None:
    """confirm=True on a non-existent span returns structured response, not
    an exception. Pins idempotency Discipline #1."""
    from contracts import RemoveSourceResponse
    from handlers.remove_source import handle_remove_source

    writer = _FakeWriter()
    client = _FakeClient({}, {}, [])
    ctx = _FakeCtx(_FakeLedger(client, writer=writer))

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


async def test_remove_source_skips_already_removed_decisions_in_cascade() -> None:
    """If a decision was previously soft-deleted (e.g., by an earlier
    remove_decision call), the cascade includes it in the returned list but
    does not re-UPDATE the row. Pins per-decision idempotency inside the
    cascade helper."""
    from handlers.remove_source import handle_remove_source

    span_id = "input_span:span001"
    span_row = {
        "text": "t",
        "source_ref": "r",
        "source_type": "manual",
        "meeting_date": "",
        "speakers": [],
        "created_at": "",
    }
    pre_existing_removed = {
        "state": "removed",
        "signer": "earlier@x",
        "reason": "earlier removal",
        "previous_state": "ratified",
    }
    decisions = {
        "decision:d1": {"signoff": pre_existing_removed},
        "decision:d2": {"signoff": {"state": "proposed"}},
    }
    edges = [(span_id, "decision:d1"), (span_id, "decision:d2")]
    client = _FakeClient({span_id: span_row}, decisions, edges)
    ctx = _FakeCtx(_FakeLedger(client))

    resp = await handle_remove_source(
        ctx, span_id=span_id, signer="kim@x", reason="cascade", confirm=True
    )

    assert set(resp.cascaded_decision_ids) == {"decision:d1", "decision:d2"}
    # d1's signoff was NOT overwritten — it kept its earlier removal record
    assert client._decisions["decision:d1"]["signoff"] == pre_existing_removed
    # d2 was newly removed
    assert client._decisions["decision:d2"]["signoff"]["state"] == "removed"
    assert client._decisions["decision:d2"]["signoff"]["reason"] == "cascade"


async def test_get_decisions_for_span_returns_yield_traversal_ids() -> None:
    """Unit test for the new ledger.queries helper. Independent of handler."""
    from ledger.queries import get_decisions_for_span

    span_id = "input_span:span001"
    edges = [(span_id, "decision:a"), (span_id, "decision:b"), ("input_span:other", "decision:c")]
    client = _FakeClient({}, {}, edges)
    result = await get_decisions_for_span(client, span_id)
    assert set(result) == {"decision:a", "decision:b"}
    assert "decision:c" not in result
