"""Allowlist + dataclass parity tests for the diagnose CLI (#252 Layer 3).

Locks the privacy posture: every Diagnosis dataclass field must appear
in _ALLOWED_FIELDS and vice versa. Any drift between the two surfaces
is caught by these tests at code-review time.
"""

from __future__ import annotations

import dataclasses

import pytest

from cli.diagnose import _ALLOWED_FIELDS, Diagnosis


def test_diagnosis_dataclass_fields_match_allowlist():
    field_names = {f.name for f in dataclasses.fields(Diagnosis)}
    assert field_names == _ALLOWED_FIELDS


def test_allowlist_excludes_known_content_field_names():
    forbidden = {
        "decision_text",
        "description",
        "source_ref",
        "text",
        "body",
        "content",
        "arguments",
        "rationale",
    }
    assert _ALLOWED_FIELDS.isdisjoint(forbidden)


def test_diagnosis_is_frozen_dataclass():
    d = Diagnosis(
        bicameral_version="0.13.8",
        python_version="3.11",
        platform_str="Linux",
        surrealdb_running="2.0.0",
        ledger_url="memory://",
        ledger_size_bytes=None,
        ledger_mtime_iso=None,
        schema_version_recorded=15,
        schema_version_expected=16,
        surrealdb_first_write=None,
        surrealdb_last_write=None,
        last_write_at=None,
        drift_status="first-write",
        audit_log_channel="stderr",
        table_counts={},
        row_probe_warnings=[],
        recent_events=[],
        suggestions=[],
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.bicameral_version = "tampered"  # type: ignore[misc]
