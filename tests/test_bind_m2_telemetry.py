"""Unit tests for the M2 telemetry emit helpers in handlers/bind.py and
handlers/resolve_compliance.py (#280 PR-3).

These tests exercise only the emit-helper indirection (handler →
m2_grounding_log) without booting the ledger, so they run locally
even when the surrealdb test dep is unavailable.

Full integration coverage of the call sites lives in tests/test_bind.py
and tests/test_resolve_compliance.py (which need the ledger).
"""

from __future__ import annotations

import importlib

import pytest


def test_bind_emit_m2_attempt_forwards_to_record_attempt(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_M2_LOG_PATH", str(tmp_path / "m2.jsonl"))
    import m2_grounding_log

    importlib.reload(m2_grounding_log)

    from handlers import bind as bind_module

    captured: list[dict] = []

    def fake_record_attempt(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(m2_grounding_log, "record_attempt", fake_record_attempt)

    bind_module._emit_m2_attempt(
        decision_id="decision:abc",
        decision_source="transcript",
        success=True,
        handler_rejected=False,
    )

    assert len(captured) == 1
    assert captured[0] == {
        "decision_id": "decision:abc",
        "decision_source": "transcript",
        "success": True,
        "handler_rejected": False,
    }


def test_bind_emit_m2_attempt_skips_when_decision_id_empty(monkeypatch, tmp_path):
    """API misuse path (empty decision_id) doesn't pollute the metric."""
    monkeypatch.setenv("BICAMERAL_M2_LOG_PATH", str(tmp_path / "m2.jsonl"))
    import m2_grounding_log

    importlib.reload(m2_grounding_log)

    from handlers import bind as bind_module

    captured: list[dict] = []
    monkeypatch.setattr(
        m2_grounding_log,
        "record_attempt",
        lambda **kw: captured.append(kw),
    )

    bind_module._emit_m2_attempt(
        decision_id="",
        decision_source=None,
        success=False,
        handler_rejected=False,
    )

    assert captured == []


def test_bind_emit_m2_attempt_swallows_telemetry_failures(monkeypatch, tmp_path):
    """A bug in the telemetry layer must not break bind."""
    monkeypatch.setenv("BICAMERAL_M2_LOG_PATH", str(tmp_path / "m2.jsonl"))
    import m2_grounding_log

    importlib.reload(m2_grounding_log)

    from handlers import bind as bind_module

    def boom(**_kw):
        raise RuntimeError("telemetry exploded")

    monkeypatch.setattr(m2_grounding_log, "record_attempt", boom)

    # Must not raise — fire-and-forget contract
    bind_module._emit_m2_attempt(
        decision_id="decision:xyz",
        decision_source="transcript",
        success=True,
        handler_rejected=False,
    )


@pytest.mark.parametrize(
    "verdict, expected_event",
    [
        ("compliant", "m2_grounding_ratified_correct"),
        ("drifted", "m2_grounding_ratified_incorrect"),
        ("not_relevant", "m2_grounding_ratified_incorrect"),
    ],
)
def test_resolve_compliance_emit_m2_ratification_classifies_verdict(
    monkeypatch, tmp_path, verdict, expected_event
):
    """compliant → ratified_correct; drifted/not_relevant → ratified_incorrect."""
    # resolve_compliance.py imports from ledger.queries (which imports
    # surrealdb at module load). Skip locally when the dep isn't available;
    # CI installs it.
    pytest.importorskip("surrealdb", reason="resolve_compliance imports ledger.queries")

    monkeypatch.setenv("BICAMERAL_M2_LOG_PATH", str(tmp_path / "m2.jsonl"))
    import m2_grounding_log

    importlib.reload(m2_grounding_log)

    captured: list[dict] = []

    def fake_record_ratification(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(m2_grounding_log, "record_ratification", fake_record_ratification)

    from handlers import resolve_compliance as rc_module

    rc_module._emit_m2_ratification(
        decision_id="decision:abc",
        decision_source="spec",
        verdict=verdict,
        confidence="high",
    )

    assert len(captured) == 1
    assert captured[0]["verdict"] == verdict
    # The classification (correct vs incorrect) happens inside record_ratification
    # (which we fully unit-test in test_m2_grounding_log.py); here we just
    # confirm the helper forwards the verdict verbatim.
