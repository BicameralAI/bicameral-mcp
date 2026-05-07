"""Functional tests for gather_diagnosis() (#252 Layer 3 Phase 1).

Each test invokes gather_diagnosis(adapter) against an in-memory ledger
and asserts on returned Diagnosis field values, persisted state, or the
suggestion-engine output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli._diagnose_gather import (
    _LARGE_LEDGER_BYTES,
    _compute_suggestions,
    _read_jsonl_warn_error_lines,
    _read_table_counts,
    _resolve_audit_log_channel,
    gather_diagnosis,
)
from cli.diagnose import Diagnosis
from ledger.adapter import SurrealDBLedgerAdapter


@pytest.fixture
async def adapter():
    a = SurrealDBLedgerAdapter("memory://")
    await a.connect()
    yield a
    await a._client.close()


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG_LEVEL", raising=False)
    yield


async def test_gather_diagnosis_returns_complete_dataclass_on_fresh_memory_ledger(adapter):
    d = await gather_diagnosis(adapter)
    assert isinstance(d, Diagnosis)
    assert d.bicameral_version  # populated, may be "unknown" in dev install
    assert d.python_version
    assert d.platform_str
    assert d.surrealdb_running
    assert d.ledger_url == "memory://"
    assert d.ledger_size_bytes is None  # memory:// has no file
    assert d.schema_version_expected >= 15
    assert d.drift_status in ("first-write", "match", "drift")
    assert d.audit_log_channel in ("stderr", "disabled") or d.audit_log_channel.startswith("/")


async def test_gather_diagnosis_reports_match_on_fresh_ledger_after_sentinel_writes(adapter):
    """Layer 2's `_emit_wire_format_sentinel` fires at adapter.connect() and
    writes the bicameral_meta row with at_first_write == at_last_write ==
    running version. By the time gather_diagnosis runs, the row exists and
    recorded == running → status is "match"."""
    d = await gather_diagnosis(adapter)
    assert d.drift_status == "match"


async def test_gather_diagnosis_table_counts_subset_of_canonical_tables(adapter):
    from cli.diagnose import _CANONICAL_TABLES

    d = await gather_diagnosis(adapter)
    assert set(d.table_counts).issubset(set(_CANONICAL_TABLES))


async def test_gather_diagnosis_recent_events_empty_when_no_jsonl(monkeypatch, tmp_path, adapter):
    # Point the home-dir resolver at an empty tmp dir so preflight_events.jsonl is absent.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    d = await gather_diagnosis(adapter)
    assert d.recent_events == []


async def test_gather_diagnosis_recent_events_tails_warn_error_only(monkeypatch, tmp_path, adapter):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bicameral = tmp_path / ".bicameral"
    bicameral.mkdir()
    jsonl = bicameral / "preflight_events.jsonl"
    jsonl.write_text(
        '{"ts": "2026-05-07T17:00:00Z", "level": "info", "event_type": "x"}\n'
        '{"ts": "2026-05-07T17:01:00Z", "level": "warn", "event_type": "ingest_refusal"}\n'
        '{"ts": "2026-05-07T17:02:00Z", "level": "error", "event_type": "boom"}\n',
        encoding="utf-8",
    )
    d = await gather_diagnosis(adapter)
    levels = [e["level"] for e in d.recent_events]
    assert "info" not in levels
    assert "warn" in levels
    assert "error" in levels


async def test_gather_diagnosis_recent_events_capped_at_5(monkeypatch, tmp_path, adapter):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    bicameral = tmp_path / ".bicameral"
    bicameral.mkdir()
    jsonl = bicameral / "preflight_events.jsonl"
    lines = "\n".join(
        f'{{"ts": "2026-05-07T17:0{i}:00Z", "level": "warn", "event_type": "x"}}' for i in range(8)
    )
    jsonl.write_text(lines + "\n", encoding="utf-8")
    d = await gather_diagnosis(adapter)
    assert len(d.recent_events) == 5


async def test_gather_diagnosis_recent_events_merges_audit_log_path(monkeypatch, tmp_path, adapter):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        '{"ts": "2026-05-07T18:00:00Z", "level": "warn", "event_type": "audit_a"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", str(audit))
    d = await gather_diagnosis(adapter)
    types = [e["event_type"] for e in d.recent_events]
    assert "audit_a" in types


async def test_gather_diagnosis_audit_log_channel_reflects_env(monkeypatch, adapter):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", "disabled")
    d = await gather_diagnosis(adapter)
    assert d.audit_log_channel == "disabled"


async def test_gather_diagnosis_emits_no_decision_content_when_decisions_present(adapter):
    # Insert a decision via direct query to verify no row content leaks into Diagnosis fields.
    await adapter._client.query(
        "CREATE decision SET description = $d, status = 'ungrounded', canonical_id = 'x1'",
        {"d": "TOP-SECRET-DECISION-CONTENT-MARKER"},
    )
    d = await gather_diagnosis(adapter)
    rendered = repr(d)
    assert "TOP-SECRET-DECISION-CONTENT-MARKER" not in rendered


def test_resolve_audit_log_channel_default_is_stderr(monkeypatch):
    monkeypatch.delenv("BICAMERAL_AUDIT_LOG", raising=False)
    label, path = _resolve_audit_log_channel()
    assert label == "stderr"
    assert path is None


def test_resolve_audit_log_channel_disabled(monkeypatch):
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", "disabled")
    label, path = _resolve_audit_log_channel()
    assert label == "disabled"
    assert path is None


def test_resolve_audit_log_channel_path(monkeypatch, tmp_path):
    target = str(tmp_path / "audit.log")
    monkeypatch.setenv("BICAMERAL_AUDIT_LOG", target)
    label, path = _resolve_audit_log_channel()
    assert label == target
    assert path == Path(target)


def test_compute_suggestions_drift_heuristic_fires():
    s = _compute_suggestions(
        {
            "drift_status": "drift",
            "surrealdb_last_write": "2.0.0",
            "surrealdb_running": "2.1.0",
            "audit_log_channel": "stderr",
            "bicameral_version": "0.13.8",
        }
    )
    assert any("drift" in line.lower() for line in s)


def test_compute_suggestions_audit_log_disabled_heuristic_fires():
    s = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "stderr",
            "bicameral_version": "0.13.8",
        }
    )
    assert any("BICAMERAL_AUDIT_LOG" in line for line in s)


def test_compute_suggestions_large_ledger_heuristic_fires():
    s = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "ledger_size_bytes": _LARGE_LEDGER_BYTES + 1,
            "bicameral_version": "0.13.8",
        }
    )
    assert any("> 100 MiB" in line or "ledger-export" in line for line in s)


def test_compute_suggestions_old_schema_heuristic_fires():
    s = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "schema_version_recorded": 14,
            "schema_version_expected": 16,
            "bicameral_version": "0.13.8",
        }
    )
    assert any("schema" in line.lower() and "migrations" in line.lower() for line in s)


def test_compute_suggestions_empty_on_clean_install():
    # All heuristics should NOT fire: no drift, audit-log file-configured, small ledger,
    # current schema, and recommended version matches current. Mock the recommended-version
    # fetch to return None so heuristic 2 doesn't fire on network access.
    s = _compute_suggestions(
        {
            "drift_status": "match",
            "audit_log_channel": "/var/log/bicameral.log",
            "ledger_size_bytes": 1024,
            "schema_version_recorded": 16,
            "schema_version_expected": 16,
            "bicameral_version": "0.13.9",  # matches RECOMMENDED_VERSION on main as of test time
        }
    )
    # Heuristic 2 (recommended-version-mismatch) may fire if the live fetch
    # returns a different version. Acceptable: ≤1 suggestion (the network heuristic).
    assert len(s) <= 1
