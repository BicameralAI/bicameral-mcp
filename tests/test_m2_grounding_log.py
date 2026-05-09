"""Unit tests for m2_grounding_log (#280 PR-3).

Pure-function tests — no ledger / surrealdb dependency. Uses
BICAMERAL_M2_LOG_PATH env override to redirect the mirror file into
pytest's tmp_path so each test runs in isolation.
"""

from __future__ import annotations

import importlib
import json

import pytest


def _reload_module_with_path(monkeypatch, tmp_path):
    """Reload m2_grounding_log so it picks up the env override."""
    log_path = tmp_path / "m2_grounding.jsonl"
    monkeypatch.setenv("BICAMERAL_M2_LOG_PATH", str(log_path))
    import m2_grounding_log as m2  # noqa: WPS433 — test-only import

    importlib.reload(m2)
    return m2, log_path


def test_record_attempt_writes_jsonl_row(monkeypatch, tmp_path):
    m2, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    m2.record_attempt(
        decision_id="decision:test-1",
        decision_source="transcript",
        success=True,
        handler_rejected=False,
    )

    assert log_path.exists()
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "m2_grounding_attempt"
    assert row["decision_id"] == "decision:test-1"
    assert row["decision_source"] == "transcript"
    assert row["success"] is True
    assert row["handler_rejected"] is False
    assert "ts" in row


def test_record_attempt_handler_rejected_path(monkeypatch, tmp_path):
    m2, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    m2.record_attempt(
        decision_id="decision:test-2",
        decision_source="spec",
        success=False,
        handler_rejected=True,
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["handler_rejected"] is True


def test_record_attempt_decision_source_none_falls_to_unknown(monkeypatch, tmp_path):
    m2, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    m2.record_attempt(
        decision_id="decision:test-3",
        decision_source=None,
        success=True,
        handler_rejected=False,
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert rows[0]["decision_source"] == "unknown"


def test_record_ratification_compliant_emits_correct_event(monkeypatch, tmp_path):
    m2, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    m2.record_ratification(
        decision_id="decision:rat-1",
        decision_source="chat",
        verdict="compliant",
        confidence="high",
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["event_type"] == "m2_grounding_ratified_correct"
    assert rows[0]["verdict"] == "compliant"
    assert rows[0]["confidence"] == 2  # "high" → 2


@pytest.mark.parametrize("verdict", ["drifted", "not_relevant"])
def test_record_ratification_incorrect_for_non_compliant(monkeypatch, tmp_path, verdict):
    m2, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    m2.record_ratification(
        decision_id=f"decision:rat-{verdict}",
        decision_source="manual",
        verdict=verdict,
        confidence="medium",
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert rows[0]["event_type"] == "m2_grounding_ratified_incorrect"
    assert rows[0]["verdict"] == verdict
    assert rows[0]["confidence"] == 1  # "medium" → 1


def test_record_ratification_unknown_confidence_defaults_to_medium(monkeypatch, tmp_path):
    m2, log_path = _reload_module_with_path(monkeypatch, tmp_path)

    m2.record_ratification(
        decision_id="decision:rat-2",
        decision_source="document",
        verdict="compliant",
        confidence=None,
    )

    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    assert rows[0]["confidence"] == 1


def test_decision_id_never_relayed_to_posthog(monkeypatch, tmp_path):
    """Privacy invariant: decision_id is local-mirror-only, not in the
    PostHog payload. record_attempt builds two separate paths — verify
    the relay path doesn't carry decision_id.
    """
    m2, _log_path = _reload_module_with_path(monkeypatch, tmp_path)

    relayed_kwargs: list[dict] = []

    def fake_send_event(*args, **kwargs):
        relayed_kwargs.append(dict(kwargs))

    # Stub out the two lazy imports inside _send_relay
    import sys
    import types

    fake_telemetry = types.ModuleType("telemetry")
    fake_telemetry.send_event = fake_send_event  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "telemetry", fake_telemetry)

    fake_server = types.ModuleType("server")
    fake_server.SERVER_VERSION = "0.0.0-test"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "server", fake_server)

    m2.record_attempt(
        decision_id="decision:secret-uuid-do-not-relay",
        decision_source="transcript",
        success=True,
        handler_rejected=False,
    )

    assert len(relayed_kwargs) == 1
    forwarded = relayed_kwargs[0]
    # The relay should see decision_source + diagnostic, NOT decision_id
    serialized = json.dumps(forwarded)
    assert "secret-uuid-do-not-relay" not in serialized
    assert forwarded.get("decision_source") == "transcript"
    assert forwarded.get("event_type") == "m2_grounding_attempt"
    assert isinstance(forwarded.get("diagnostic"), dict)
    assert "decision_id" not in forwarded
