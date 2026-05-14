"""Phase 3 — admin SurrealQL route tests.

Pins (Phase 3 Security Disciplines #1–#6 + audit Pass 2 amendments):
  1. Route returns 404 when BICAMERAL_ENABLE_ADMIN_PANEL is unset.
  2. Foreign-origin requests are rejected 403; missing origin → 403.
  3. Read-only mode wraps SQL in BEGIN/CANCEL TRANSACTION.
  4. Write mode rejected without BICAMERAL_ENABLE_ADMIN_PANEL_WRITES.
  5. Write mode rejected with empty/whitespace signer (audit-trail obligation).
  6. Every executed query emits audit event — team writer if attached,
     otherwise local `<repo>/.bicameral/events/_admin.jsonl`.
  7. Error path captures the exception in response.error AND the audit event.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


# ── helpers ───────────────────────────────────────────────────────────────


class _FakeClient:
    """Records every query and returns canned rows."""

    def __init__(self, response_rows=None, raise_on=None) -> None:
        self._rows = response_rows if response_rows is not None else []
        self._raise_on = raise_on  # substring; if present in SQL, raise
        self.queries: list[str] = []

    async def query(self, sql: str, params=None):
        self.queries.append(sql)
        if self._raise_on and self._raise_on in sql:
            raise RuntimeError(f"simulated failure: {self._raise_on}")
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


# ── helper unit tests ─────────────────────────────────────────────────────


def test_check_admin_origin_strict_match() -> None:
    from dashboard.admin import check_admin_origin

    assert check_admin_origin("http://localhost:12345", 12345) is True
    assert check_admin_origin("http://localhost:12345", 9999) is False
    assert check_admin_origin("http://evil.local", 12345) is False
    assert check_admin_origin("", 12345) is False
    assert check_admin_origin(None, 12345) is False


def test_wrap_read_only_emits_begin_cancel() -> None:
    from dashboard.admin import wrap_read_only

    wrapped = wrap_read_only("SELECT * FROM decision")
    assert wrapped.startswith("BEGIN TRANSACTION")
    assert "CANCEL TRANSACTION" in wrapped
    assert "SELECT * FROM decision" in wrapped


def test_emit_admin_event_local_writes_jsonl(tmp_path: Path) -> None:
    from dashboard.admin import emit_admin_event_local

    payload = {"sql": "SELECT 1", "mode": "read-only", "signer": ""}
    out = emit_admin_event_local(payload, tmp_path)
    assert out.exists()
    assert out.parent.name == "events"
    assert out.parent.parent.name == ".bicameral"
    assert out.name == "_admin.jsonl"
    line = out.read_text(encoding="utf-8").strip()
    decoded = json.loads(line)
    assert decoded["event_type"] == "admin_query.executed"
    assert decoded["payload"] == payload
    # Second event appends without overwriting
    emit_admin_event_local({"sql": "SELECT 2", "mode": "write", "signer": "x"}, tmp_path)
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2


# ── env-flag gating ──────────────────────────────────────────────────────


async def test_admin_route_returns_404_without_env_flag(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.delenv("BICAMERAL_ENABLE_ADMIN_PANEL", raising=False)
    client = _FakeClient()
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "SELECT 1", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 404
    assert "not enabled" in body["error"]
    # No DB call when route is disabled
    assert client.queries == []


async def test_admin_route_rejects_foreign_origin(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    client = _FakeClient()
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "SELECT 1", "mode": "read"},
        origin="http://evil.local",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 403
    assert "Origin not permitted" in body["error"]
    assert client.queries == []


async def test_admin_route_rejects_missing_origin(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    client = _FakeClient()
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "SELECT 1", "mode": "read"},
        origin=None,
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 403


# ── read-only execution path ──────────────────────────────────────────────


async def test_admin_read_only_query_wraps_in_transaction(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.delenv("BICAMERAL_ENABLE_ADMIN_PANEL_WRITES", raising=False)
    client = _FakeClient(response_rows=[{"id": "decision:abc"}])
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "SELECT * FROM decision", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 200
    assert body["mode"] == "read-only"
    assert body["rows"] == [{"id": "decision:abc"}]
    # SQL was wrapped in BEGIN/CANCEL
    assert len(client.queries) == 1
    assert "BEGIN TRANSACTION" in client.queries[0]
    assert "CANCEL TRANSACTION" in client.queries[0]


# ── write-mode gating ────────────────────────────────────────────────────


async def test_admin_write_rejected_without_writes_flag(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.delenv("BICAMERAL_ENABLE_ADMIN_PANEL_WRITES", raising=False)
    client = _FakeClient()
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "UPDATE decision:x SET x = 1", "mode": "write", "signer": "kim@x"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 403
    assert "WRITES" in body["error"]
    assert client.queries == []


async def test_admin_write_rejects_empty_signer(monkeypatch, tmp_path: Path) -> None:
    """Audit Pass 1 Finding 2 — write mode requires non-empty signer."""
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL_WRITES", "1")
    client = _FakeClient()
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "UPDATE decision:x SET x = 1", "mode": "write", "signer": ""},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 400
    assert "signer" in body["error"].lower()
    assert client.queries == []


async def test_admin_write_rejects_whitespace_only_signer(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL_WRITES", "1")
    client = _FakeClient()
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={"sql": "UPDATE x", "mode": "write", "signer": "   \t\n"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 400
    assert client.queries == []


async def test_admin_write_executes_when_both_flags_and_signer_set(
    monkeypatch, tmp_path: Path
) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL_WRITES", "1")
    client = _FakeClient(response_rows=[{"updated": 1}])
    ledger = _FakeLedger(client)
    status, body = await process_admin_query(
        payload_in={
            "sql": "UPDATE decision:abc SET feature_group = 'test'",
            "mode": "write",
            "signer": "kim@example.com",
        },
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    assert status == 200
    assert body["mode"] == "write"
    # Write mode runs the SQL directly — no BEGIN/CANCEL wrap
    assert "BEGIN TRANSACTION" not in client.queries[0]
    assert "UPDATE decision:abc" in client.queries[0]


# ── audit-log obligation (audit Pass 1 Finding 1) ─────────────────────────


async def test_admin_query_emits_audit_event_in_team_mode(monkeypatch, tmp_path: Path) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    writer = _FakeWriter()
    client = _FakeClient(response_rows=[{"id": "x"}])
    ledger = _FakeLedger(client, writer=writer)

    await process_admin_query(
        payload_in={"sql": "SELECT * FROM decision", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )

    assert len(writer.events) == 1
    event_type, payload = writer.events[0]
    assert event_type == "admin_query.executed"
    assert payload["sql"] == "SELECT * FROM decision"
    assert payload["mode"] == "read-only"
    assert payload["error"] is None
    assert "elapsed_ms" in payload
    # In team mode the LOCAL audit file is NOT written (event goes through writer)
    assert not (tmp_path / ".bicameral" / "events" / "_admin.jsonl").exists()


async def test_admin_query_emits_local_audit_file_when_no_team_writer(
    monkeypatch, tmp_path: Path
) -> None:
    """Audit Pass 1 Finding 1 — no unaudited admin path exists in local-only mode."""
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    client = _FakeClient(response_rows=[{"id": "x"}])
    ledger = _FakeLedger(client)  # NO writer attached
    assert not hasattr(ledger, "_writer")

    await process_admin_query(
        payload_in={"sql": "SELECT * FROM decision", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )

    audit_path = tmp_path / ".bicameral" / "events" / "_admin.jsonl"
    assert audit_path.exists(), "local-mode admin queries must fall back to _admin.jsonl"
    lines = audit_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    decoded = json.loads(lines[0])
    assert decoded["event_type"] == "admin_query.executed"
    assert decoded["payload"]["sql"] == "SELECT * FROM decision"
    assert decoded["payload"]["mode"] == "read-only"


async def test_admin_query_error_path_emits_audit_event_with_error_field(
    monkeypatch, tmp_path: Path
) -> None:
    from dashboard.admin import process_admin_query

    monkeypatch.setenv("BICAMERAL_ENABLE_ADMIN_PANEL", "1")
    writer = _FakeWriter()
    client = _FakeClient(raise_on="BAD_SYNTAX")
    ledger = _FakeLedger(client, writer=writer)

    status, body = await process_admin_query(
        payload_in={"sql": "BAD_SYNTAX !!", "mode": "read"},
        origin="http://localhost:8080",
        dashboard_port=8080,
        ledger=ledger,
        repo_path=tmp_path,
    )
    # The handler returns 200 + error field rather than 500 — the error
    # belongs in the response body so the operator sees the SurrealDB
    # error message in the UI.
    assert status == 200
    assert body["error"] is not None
    assert "simulated failure" in body["error"]
    # Audit event still emitted with the error captured
    assert len(writer.events) == 1
    assert writer.events[0][1]["error"] == body["error"]
