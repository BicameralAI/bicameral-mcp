"""Functional tests for the @server.call_tool() audit-log wrapper (#227 Phase 2).

Each test invokes ``server.call_tool`` (the outer wrapper) and asserts
on captured emit args. The inner ``_call_tool_impl`` is monkeypatched
so tests don't need full ledger / repo / dashboard scaffolding.
"""

from __future__ import annotations

import asyncio

import pytest

import audit_log
import server as server_mod
from audit_log import AuditEventType
from handlers.ingest import _IngestRefused


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    yield
    audit_log._reset_for_tests()


def _captured_emit_calls(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []

    def _stub(event_type, **kwargs):
        calls.append((event_type, kwargs))

    monkeypatch.setattr(audit_log, "emit", _stub)
    return calls


def _stub_impl(monkeypatch, behavior: str = "ok", *, returns=None):
    """Replace ``_call_tool_impl`` with a controllable stub."""

    async def _impl(name, arguments):
        if behavior == "ok":
            return returns or []
        if behavior == "refused":
            raise _IngestRefused("test_refused", detail="from stub")
        if behavior == "error":
            raise RuntimeError("synthetic error")
        raise AssertionError(f"unknown behavior {behavior!r}")

    monkeypatch.setattr(server_mod, "_call_tool_impl", _impl)


def test_tool_invocation_emits_with_tool_name_and_duration_ms(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch)
    asyncio.run(server_mod.call_tool("bicameral.history", {"session_id": "s1"}))
    invocation_calls = [c for c in calls if c[0] == AuditEventType.TOOL_INVOCATION]
    assert len(invocation_calls) == 1
    kwargs = invocation_calls[0][1]
    assert kwargs["tool_name"] == "bicameral.history"
    assert isinstance(kwargs["duration_ms"], int)
    assert kwargs["duration_ms"] >= 0


def test_tool_invocation_outcome_class_is_ok_on_normal_return(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch, "ok")
    asyncio.run(server_mod.call_tool("bicameral.history", {"session_id": "s1"}))
    kwargs = next(c[1] for c in calls if c[0] == AuditEventType.TOOL_INVOCATION)
    assert kwargs["outcome_class"] == "ok"


def test_tool_invocation_outcome_class_is_refused_on_ingest_refused(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch, "refused")
    with pytest.raises(_IngestRefused):
        asyncio.run(server_mod.call_tool("bicameral.ingest", {"session_id": "s1"}))
    kwargs = next(c[1] for c in calls if c[0] == AuditEventType.TOOL_INVOCATION)
    assert kwargs["outcome_class"] == "refused"


def test_tool_invocation_outcome_class_is_error_on_unexpected_exception(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch, "error")
    with pytest.raises(RuntimeError):
        asyncio.run(server_mod.call_tool("bicameral.history", {"session_id": "s1"}))
    kwargs = next(c[1] for c in calls if c[0] == AuditEventType.TOOL_INVOCATION)
    assert kwargs["outcome_class"] == "error"


def test_tool_invocation_emit_does_not_include_arguments(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch)
    args = {"session_id": "s1", "secret": "do-not-leak", "query": "internal"}
    asyncio.run(server_mod.call_tool("bicameral.history", args))
    kwargs = next(c[1] for c in calls if c[0] == AuditEventType.TOOL_INVOCATION)
    assert "arguments" not in kwargs
    assert "secret" not in kwargs
    assert "query" not in kwargs


def test_tool_invocation_session_id_extracted_from_arguments_when_present(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch)
    asyncio.run(server_mod.call_tool("bicameral.history", {"session_id": "abc-123"}))
    kwargs = next(c[1] for c in calls if c[0] == AuditEventType.TOOL_INVOCATION)
    assert kwargs["session_id"] == "abc-123"


def test_tool_invocation_session_id_omitted_when_arguments_lack_field(monkeypatch):
    calls = _captured_emit_calls(monkeypatch)
    _stub_impl(monkeypatch)
    asyncio.run(server_mod.call_tool("bicameral.history", {}))
    kwargs = next(c[1] for c in calls if c[0] == AuditEventType.TOOL_INVOCATION)
    assert kwargs.get("session_id") is None
