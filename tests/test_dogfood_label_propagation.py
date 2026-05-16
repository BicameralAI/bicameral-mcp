"""Phase 4 — dogfood label propagation tests.

Pins:
  1. The `dogfood_label` field is OMITTED from event payloads when
     BICAMERAL_DOGFOOD_LABEL is unset.
  2. The field is OMITTED when the env var is set to an empty string
     (empty-string env is treated as noise, not signal).
  3. The field is PRESENT and matches the env value for all three
     Phase 1–3 emitter sites: remove_decision, remove_source,
     admin_query (both team-mode and local-mode paths).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


# ── pure helper unit tests ────────────────────────────────────────────────


def test_maybe_dogfood_label_omits_when_env_unset(monkeypatch) -> None:
    from events.dogfood import maybe_dogfood_label

    monkeypatch.delenv("BICAMERAL_DOGFOOD_LABEL", raising=False)
    out = maybe_dogfood_label({"a": 1})
    assert "dogfood_label" not in out


def test_maybe_dogfood_label_omits_when_env_empty(monkeypatch) -> None:
    """Empty-string env values are noise, not signal — handler must not
    add the field. Pinned because operators sometimes accidentally set
    env vars to empty when they meant to unset them."""
    from events.dogfood import maybe_dogfood_label

    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "")
    out = maybe_dogfood_label({"a": 1})
    assert "dogfood_label" not in out


def test_maybe_dogfood_label_omits_when_env_whitespace(monkeypatch) -> None:
    from events.dogfood import maybe_dogfood_label

    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "   \t\n")
    out = maybe_dogfood_label({"a": 1})
    assert "dogfood_label" not in out


def test_maybe_dogfood_label_adds_when_env_set(monkeypatch) -> None:
    from events.dogfood import maybe_dogfood_label

    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "partner-acme")
    out = maybe_dogfood_label({"a": 1})
    assert out["dogfood_label"] == "partner-acme"
    assert out["a"] == 1  # original fields preserved


# ── reuse the Phase 2 fake-ledger pattern for handler emitter tests ───────


class _FakeClient:
    def __init__(
        self,
        decisions: dict | None = None,
        spans: dict | None = None,
        edges: list | None = None,
        response_rows=None,
    ) -> None:
        self._decisions = decisions or {}
        self._spans = spans or {}
        self._edges = list(edges or [])
        self._rows = response_rows or []
        self.queries: list[str] = []

    async def query(self, sql: str, params=None):
        self.queries.append(sql)
        sql_l = sql.lower()
        # decision_exists
        if "select id from decision:" in sql_l and "limit 1" in sql_l:
            did = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return [{"id": did}] if did in self._decisions else []
        # input_span_exists
        if "select id from input_span:" in sql_l and "limit 1" in sql_l:
            sid = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return [{"id": sid}] if sid in self._spans else []
        # span row
        if sql_l.startswith("select text, source_ref, source_type"):
            sid = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            row = self._spans.get(sid)
            return [row] if row else []
        # span->decisions
        if "<-yields<-input_span contains" in sql_l:
            sid = sql.split("CONTAINS ", 1)[1].strip()
            return [{"decision_id": d} for (s, d) in self._edges if s == sid]
        # signoff read
        if "select signoff from decision:" in sql_l:
            did = sql.split("FROM ", 1)[1].split(" ", 1)[0]
            return [{"signoff": self._decisions.get(did, {}).get("signoff")}]
        # UPDATE
        if "update decision:" in sql_l and "set signoff" in sql_l:
            did = sql.split("UPDATE ", 1)[1].split(" SET")[0].strip()
            row = self._decisions.setdefault(did, {})
            row["signoff"] = params["signoff"]
            return [row]
        # DELETE yields/span
        if sql_l.startswith("delete yields"):
            sid = sql.rsplit("=", 1)[1].strip()
            self._edges = [(s, d) for (s, d) in self._edges if s != sid]
            return []
        if sql_l.startswith("delete input_span:"):
            sid = sql.split("DELETE ", 1)[1].strip()
            self._spans.pop(sid, None)
            return []
        return list(self._rows)


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
    def __init__(self, ledger, session_id="s", sha="d") -> None:
        self.ledger = ledger
        self.session_id = session_id
        self.authoritative_sha = sha


@pytest.fixture(autouse=True)
def _stub_queries(monkeypatch):
    """Stub status-projection helpers so handler tests don't need full schema."""

    async def _decision_exists(client, did):
        return did in client._decisions

    async def _project(client, did):
        return "ungrounded"

    async def _update(client, did, status):
        return None

    monkeypatch.setattr("handlers.remove_decision.decision_exists", _decision_exists)
    # remove_decision no longer calls project/update_decision_status — the
    # v0.15.x hard-delete path drops them; only remove_source still uses
    # them (cascade still does soft-delete pending its own decision).
    monkeypatch.setattr("handlers.remove_source.project_decision_status", _project)
    monkeypatch.setattr("handlers.remove_source.update_decision_status", _update)


# ── remove_decision emitter ───────────────────────────────────────────────


async def test_remove_decision_event_omits_dogfood_label_by_default(
    monkeypatch,
) -> None:
    from handlers.remove_decision import handle_remove_decision

    monkeypatch.delenv("BICAMERAL_DOGFOOD_LABEL", raising=False)
    writer = _FakeWriter()
    ledger = _FakeLedger(
        _FakeClient(decisions={"decision:abc": {"signoff": {"state": "ratified"}}}),
        writer=writer,
    )
    await handle_remove_decision(
        _FakeCtx(ledger), decision_id="decision:abc", signer="x", reason="r"
    )
    assert len(writer.events) == 1
    _, payload = writer.events[0]
    assert "dogfood_label" not in payload


async def test_remove_decision_event_includes_dogfood_label_when_env_set(
    monkeypatch,
) -> None:
    from handlers.remove_decision import handle_remove_decision

    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "partner-acme")
    writer = _FakeWriter()
    ledger = _FakeLedger(
        _FakeClient(decisions={"decision:abc": {"signoff": {"state": "ratified"}}}),
        writer=writer,
    )
    await handle_remove_decision(
        _FakeCtx(ledger), decision_id="decision:abc", signer="x", reason="r"
    )
    _, payload = writer.events[0]
    assert payload["dogfood_label"] == "partner-acme"


# ── remove_source emitter ─────────────────────────────────────────────────


async def test_remove_source_event_includes_dogfood_label_when_env_set(
    monkeypatch,
) -> None:
    from handlers.remove_source import handle_remove_source

    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "partner-acme")
    span_id = "input_span:s1"
    spans = {
        span_id: {
            "text": "t",
            "source_ref": "r",
            "source_type": "manual",
            "meeting_date": "",
            "speakers": [],
            "created_at": "",
        }
    }
    decisions = {"decision:d1": {"signoff": {"state": "ratified"}}}
    edges = [(span_id, "decision:d1")]
    writer = _FakeWriter()
    ledger = _FakeLedger(_FakeClient(decisions=decisions, spans=spans, edges=edges), writer=writer)

    await handle_remove_source(
        _FakeCtx(ledger), span_id=span_id, signer="x", reason="r", confirm=True
    )
    assert len(writer.events) == 1
    _, payload = writer.events[0]
    assert payload["dogfood_label"] == "partner-acme"


# ── admin_query emitter (team mode + local mode) ──────────────────────────


async def test_admin_query_event_includes_dogfood_label_when_env_set_team_mode(
    monkeypatch, tmp_path: Path
) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "partner-acme")
    writer = _FakeWriter()
    ledger = _FakeLedger(_FakeClient(response_rows=[{"x": 1}]), writer=writer)
    await process_admin_query(
        payload_in={"sql": "SELECT 1", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert len(writer.events) == 1
    _, payload = writer.events[0]
    assert payload["dogfood_label"] == "partner-acme"


async def test_admin_query_event_includes_dogfood_label_when_env_set_local_mode(
    monkeypatch, tmp_path: Path
) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.setenv("BICAMERAL_DOGFOOD_LABEL", "partner-acme")
    ledger = _FakeLedger(_FakeClient(response_rows=[{"x": 1}]))  # no writer
    await process_admin_query(
        payload_in={"sql": "SELECT 1", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    audit_path = tmp_path / ".bicameral" / "events" / "_admin.jsonl"
    line = audit_path.read_text(encoding="utf-8").strip().split("\n")[0]
    decoded = json.loads(line)
    assert decoded["payload"]["dogfood_label"] == "partner-acme"
