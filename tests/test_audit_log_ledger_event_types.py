"""Functional tests for the new LEDGER_* AuditEventType values (#252 Layer 2)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path

import pytest

import audit_log
from audit_log import AuditEventType, emit


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


def test_audit_event_type_includes_ledger_schema_verified():
    assert AuditEventType.LEDGER_SCHEMA_VERIFIED.value == "ledger_schema_verified"


def test_audit_event_type_includes_ledger_version_drift():
    assert AuditEventType.LEDGER_VERSION_DRIFT.value == "ledger_version_drift"


def test_emit_ledger_schema_verified_renders_at_info_level():
    record = _capture_emit(
        AuditEventType.LEDGER_SCHEMA_VERIFIED,
        status="match",
        surrealdb_client_version_running="2.0.0",
        bicameral_schema_version=16,
    )
    assert record["level"] == "info"
    assert record["event_type"] == "ledger_schema_verified"
    assert record["status"] == "match"
    assert record["surrealdb_client_version_running"] == "2.0.0"
    assert record["bicameral_schema_version"] == 16


def test_emit_ledger_version_drift_renders_at_warn_level():
    record = _capture_emit(
        AuditEventType.LEDGER_VERSION_DRIFT,
        surrealdb_client_version_recorded="2.0.0",
        surrealdb_client_version_running="2.1.0",
        bicameral_schema_version=16,
    )
    assert record["level"] == "warn"
    assert record["event_type"] == "ledger_version_drift"
    assert record["surrealdb_client_version_recorded"] == "2.0.0"
    assert record["surrealdb_client_version_running"] == "2.1.0"


def test_emit_ledger_version_drift_passes_warn_filter_when_min_level_is_warn(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG_LEVEL", "warn")
    audit_log._reset_for_tests()
    record = _capture_emit(
        AuditEventType.LEDGER_VERSION_DRIFT,
        surrealdb_client_version_recorded="2.0.0",
        surrealdb_client_version_running="2.1.0",
        bicameral_schema_version=16,
    )
    assert record["event_type"] == "ledger_version_drift"


def test_emit_ledger_schema_verified_dropped_when_min_level_is_warn(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG_LEVEL", "warn")
    audit_log._reset_for_tests()
    buf = io.StringIO()
    with redirect_stderr(buf):
        emit(
            AuditEventType.LEDGER_SCHEMA_VERIFIED,
            status="match",
            surrealdb_client_version_running="2.0.0",
            bicameral_schema_version=16,
        )
    assert buf.getvalue() == ""


def test_audit_log_policy_doc_documents_new_event_types():
    """Doc/code drift lock — both new event types must appear in the policy doc."""
    repo_root = Path(__file__).resolve().parent.parent
    doc = repo_root / "docs" / "policies" / "audit-log.md"
    content = doc.read_text(encoding="utf-8")
    assert "ledger_schema_verified" in content
    assert "ledger_version_drift" in content
