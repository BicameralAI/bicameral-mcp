"""Functional tests for ingest-refusal dual-write (#227 Phase 2).

Verifies bidirectional exception isolation: a failure on either the
JSONL telemetry write or the audit-log emit MUST NOT block the other.
The original ``_IngestRefused`` propagates cleanly via the caller's
``raise``.

The plan's preflight bypass dual-write was a deferred deviation — the
v1 bypass surface is reverted (commit ``d1e3914``) so there is no active
caller of ``preflight_telemetry.write_bypass_event`` to wire. When the
v1 surface returns, the same dual-write helper pattern from
``handlers.ingest._emit_ingest_refusal_telemetry`` becomes the template.
"""

from __future__ import annotations

import pytest

import audit_log
from handlers import ingest as ingest_mod


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    audit_log._reset_for_tests()
    yield
    audit_log._reset_for_tests()


def test_ingest_refusal_writes_both_jsonl_and_audit_log(monkeypatch):
    jsonl_calls: list[tuple] = []
    audit_calls: list[tuple] = []

    def _jsonl_stub(reason, session_id):
        jsonl_calls.append((reason, session_id))

    def _audit_stub(event_type, **kwargs):
        audit_calls.append((event_type, kwargs))

    monkeypatch.setattr(ingest_mod.preflight_telemetry, "write_ingest_refusal_event", _jsonl_stub)
    monkeypatch.setattr(audit_log, "emit", _audit_stub)

    ingest_mod._emit_ingest_refusal_telemetry("size_limit_exceeded", "session-1")

    assert jsonl_calls == [("size_limit_exceeded", "session-1")]
    assert len(audit_calls) == 1
    event_type, kwargs = audit_calls[0]
    assert event_type.value == "ingest_refusal"
    assert kwargs["reason"] == "size_limit_exceeded"
    assert kwargs["session_id"] == "session-1"


def test_audit_log_write_failure_does_not_break_jsonl_write(monkeypatch):
    """When audit_log.emit raises (test-monkeypatch case bypassing internal
    isolation), the JSONL write must still complete and the helper must
    return normally so the caller's ``raise`` propagates the original
    ``_IngestRefused`` exception."""
    jsonl_calls: list[tuple] = []

    def _jsonl_stub(reason, session_id):
        jsonl_calls.append((reason, session_id))

    def _audit_explode(event_type, **kwargs):
        raise RuntimeError("audit-log surface failure")

    monkeypatch.setattr(ingest_mod.preflight_telemetry, "write_ingest_refusal_event", _jsonl_stub)
    monkeypatch.setattr(audit_log, "emit", _audit_explode)

    # Helper must not raise — exception isolation contract.
    ingest_mod._emit_ingest_refusal_telemetry("rate_limit_exceeded", "session-2")

    assert jsonl_calls == [("rate_limit_exceeded", "session-2")]


def test_jsonl_write_failure_does_not_break_audit_log_write(monkeypatch):
    """When the JSONL writer raises, the audit-log emit must still fire and
    the helper must return normally."""
    audit_calls: list[tuple] = []

    def _jsonl_explode(reason, session_id):
        raise OSError("disk-full simulation")

    def _audit_stub(event_type, **kwargs):
        audit_calls.append((event_type, kwargs))

    monkeypatch.setattr(
        ingest_mod.preflight_telemetry, "write_ingest_refusal_event", _jsonl_explode
    )
    monkeypatch.setattr(audit_log, "emit", _audit_stub)

    # Helper must not raise — exception isolation contract.
    ingest_mod._emit_ingest_refusal_telemetry("injection_canary_match", "session-3")

    assert len(audit_calls) == 1
    event_type, kwargs = audit_calls[0]
    assert event_type.value == "ingest_refusal"
    assert kwargs["reason"] == "injection_canary_match"
    assert kwargs["session_id"] == "session-3"
