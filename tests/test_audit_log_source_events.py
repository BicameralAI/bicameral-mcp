"""Tests for #419 Phase 0b — new AuditEventType members + level mapping.

Phase 0b ships the vocabulary; per-source adapters (Phase 1+) add the
emit sites. These tests assert the enum + level mapping is wired so a
future PR can rely on it without fragile string-comparison checks.
"""

from __future__ import annotations

import pytest

from audit_log import _LEVEL_BY_EVENT, AuditEventType

_SOURCE_EVENTS = [
    AuditEventType.SOURCE_INGEST_ATTEMPT,
    AuditEventType.SOURCE_INGEST_ACCEPTED,
    AuditEventType.SOURCE_INGEST_REFUSED,
    AuditEventType.SOURCE_AUTH_GRANTED,
    AuditEventType.SOURCE_AUTH_REVOKED,
]


@pytest.mark.parametrize("ev", _SOURCE_EVENTS)
def test_source_event_has_level_mapping(ev):
    assert ev in _LEVEL_BY_EVENT, f"{ev} missing from _LEVEL_BY_EVENT"
    level = _LEVEL_BY_EVENT[ev]
    assert level in {"info", "warn", "error"}


def test_attempt_and_accepted_are_info():
    """High-volume routine events stay at info — operators filtering on
    warn shouldn't drown in ingest attempt logs."""
    assert _LEVEL_BY_EVENT[AuditEventType.SOURCE_INGEST_ATTEMPT] == "info"
    assert _LEVEL_BY_EVENT[AuditEventType.SOURCE_INGEST_ACCEPTED] == "info"


def test_refusal_and_auth_lifecycle_are_warn():
    """Operator-actionable events surface at warn — refusals indicate
    problems, auth grants/revokes are security-relevant."""
    assert _LEVEL_BY_EVENT[AuditEventType.SOURCE_INGEST_REFUSED] == "warn"
    assert _LEVEL_BY_EVENT[AuditEventType.SOURCE_AUTH_GRANTED] == "warn"
    assert _LEVEL_BY_EVENT[AuditEventType.SOURCE_AUTH_REVOKED] == "warn"


def test_string_values_are_stable():
    """Enum string values are part of the operator-facing audit-log
    schema — assert them explicitly so a rename triggers a test failure."""
    assert AuditEventType.SOURCE_INGEST_ATTEMPT.value == "source_ingest_attempt"
    assert AuditEventType.SOURCE_INGEST_ACCEPTED.value == "source_ingest_accepted"
    assert AuditEventType.SOURCE_INGEST_REFUSED.value == "source_ingest_refused"
    assert AuditEventType.SOURCE_AUTH_GRANTED.value == "source_auth_granted"
    assert AuditEventType.SOURCE_AUTH_REVOKED.value == "source_auth_revoked"
