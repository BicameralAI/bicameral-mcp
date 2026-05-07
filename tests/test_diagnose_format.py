"""Functional tests for format_diagnosis() markdown rendering (#252 Layer 3 Phase 2)."""

from __future__ import annotations

import pytest

from cli.diagnose import Diagnosis, format_diagnosis


def _fixture_diagnosis(**overrides) -> Diagnosis:
    base = {
        "bicameral_version": "0.13.8",
        "python_version": "3.11.5",
        "platform_str": "Linux-6.6-x86_64",
        "surrealdb_running": "2.0.0",
        "ledger_url": "surrealkv:///home/u/.bicameral/ledger.db",
        "ledger_size_bytes": 4_194_304,
        "ledger_mtime_iso": "2026-05-07T16:42:11+00:00",
        "schema_version_recorded": 16,
        "schema_version_expected": 16,
        "surrealdb_first_write": "2.0.0",
        "surrealdb_last_write": "2.0.0",
        "last_write_at": "2026-05-07T16:42:11+00:00",
        "drift_status": "match",
        "audit_log_channel": "stderr",
        "table_counts": {"decision": 47, "code_region": 89},
        "recent_events": [],
        "suggestions": [],
    }
    base.update(overrides)
    return Diagnosis(**base)


def test_format_diagnosis_emits_required_section_headers():
    out = format_diagnosis(_fixture_diagnosis())
    assert "## Versions" in out
    assert "## Ledger" in out
    assert "## Schema revision sentinel" in out
    assert "## Table row counts" in out
    assert "## Recent events" in out
    assert "## Suggested remediation" in out


def test_format_diagnosis_emits_versions_section_with_all_three():
    out = format_diagnosis(_fixture_diagnosis())
    assert "0.13.8" in out
    assert "3.11.5" in out
    assert "2.0.0" in out


def test_format_diagnosis_emits_table_counts_as_indented_list():
    out = format_diagnosis(_fixture_diagnosis())
    assert "decision: 47" in out
    assert "code_region: 89" in out


def test_format_diagnosis_emits_drift_status_uppercased():
    out = format_diagnosis(_fixture_diagnosis(drift_status="drift"))
    assert "DRIFT" in out


def test_format_diagnosis_emits_recent_events_with_event_type_only():
    evt = {"ts": "2026-05-07T17:00:00Z", "level": "warn", "event_type": "ingest_refusal"}
    out = format_diagnosis(_fixture_diagnosis(recent_events=[evt]))
    assert "ingest_refusal" in out
    assert "decision_text" not in out
    assert "description" not in out


def test_format_diagnosis_emits_paste_instruction_footer():
    out = format_diagnosis(_fixture_diagnosis())
    assert "github.com/BicameralAI/bicameral-mcp/issues" in out
    assert "no decision content" in out


def test_format_diagnosis_renders_empty_suggestions_as_clean_install_message():
    out = format_diagnosis(_fixture_diagnosis(suggestions=[]))
    assert "No issues detected" in out


def test_format_diagnosis_does_not_emit_any_forbidden_content_field_names():
    out = format_diagnosis(
        _fixture_diagnosis(
            recent_events=[
                {"ts": "x", "level": "warn", "event_type": "ingest_refusal"},
            ],
            suggestions=["test suggestion"],
        )
    )
    forbidden = ["decision_text", "description", "source_ref", "transcript", "rationale"]
    for f in forbidden:
        assert f not in out, f"forbidden field name {f!r} appeared in rendered output"
