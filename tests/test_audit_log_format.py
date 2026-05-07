"""Functional tests for the audit_log JsonFormatter + emit() shaping (#227)."""

from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stderr

import pytest

import audit_log
from audit_log import AuditEventType, JsonFormatter, emit


@pytest.fixture(autouse=True)
def _reset_audit_log_state(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    yield
    audit_log._reset_for_tests()


def _capture_emit(*args, **kwargs) -> dict:
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(*args, **kwargs)
    line = buf.getvalue().strip()
    return json.loads(line) if line else {}


def test_json_formatter_emits_valid_json_with_required_fields():
    record = logging.makeLogRecord({})
    record.audit_payload = {  # type: ignore[attr-defined]
        "ts": 1.5,
        "level": "info",
        "event_type": "tool_invocation",
        "message": "hi",
    }
    formatted = JsonFormatter().format(record)
    parsed = json.loads(formatted)
    assert parsed["ts"] == 1.5
    assert parsed["level"] == "info"
    assert parsed["event_type"] == "tool_invocation"
    assert parsed["message"] == "hi"


def test_emit_omits_session_id_when_not_provided():
    record = _capture_emit(AuditEventType.SERVER_START, message="boot")
    assert "session_id" not in record


def test_emit_includes_session_id_when_provided():
    record = _capture_emit(AuditEventType.TOOL_INVOCATION, session_id="abc", duration_ms=42)
    assert record["session_id"] == "abc"
    assert record["duration_ms"] == 42


def test_emit_unknown_event_type_string_is_coerced_to_error_with_original_field():
    record = _capture_emit("typo_event", message="x")
    assert record["event_type"] == "error"
    assert record["original_event_type"] == "typo_event"


def test_emit_with_enum_value_uses_enum_string():
    record = _capture_emit(AuditEventType.TOOL_INVOCATION)
    assert record["event_type"] == "tool_invocation"


def test_emit_swallows_exceptions_and_writes_marker_to_stderr(monkeypatch):
    def _explode():
        raise RuntimeError("synthetic")

    monkeypatch.setattr(audit_log, "_get_logger", _explode)
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(AuditEventType.TOOL_INVOCATION)
    output = buf.getvalue()
    assert "audit_log emit failed" in output


def test_emit_below_min_level_is_dropped(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG_LEVEL", "warn")
    audit_log._reset_for_tests()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(AuditEventType.TOOL_INVOCATION, message="info-event")
    assert buf.getvalue() == ""


def test_emit_at_or_above_min_level_passes(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG_LEVEL", "warn")
    audit_log._reset_for_tests()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(AuditEventType.INGEST_REFUSAL, reason="x")
    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["level"] == "warn"
    assert parsed["event_type"] == "ingest_refusal"
