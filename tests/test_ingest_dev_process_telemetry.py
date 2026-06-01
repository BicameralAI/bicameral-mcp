"""Sociable tests for the #393 ``g2_dropped_dev_process`` ingest counter.

These run the real ``_handle_skill_end_impl`` validation path and seam off only
``telemetry.record_skill_event`` — the external PostHog sink we cannot run in
tests (CLAUDE.md sociable-testing rule 4/5: "external boundaries we can't run").
We assert observable behavior — whether the field is accepted (no
``diagnostic_warning``) and round-trips into the recorded diagnostic — not a mock
call signature, so the test is not a tautology.

The negative control (``g2_bogus``) proves the assertion would fail if the field
were *not* declared on ``IngestDiagnostic`` in ``contracts.py`` — the exact
silent-drop failure mode the model's ``extra="forbid"`` guards against.
"""

from __future__ import annotations

from typing import Any

import pytest

import telemetry
from handlers.skill import _handle_skill_end_impl


@pytest.fixture
def captured_events(monkeypatch):
    """Capture every record_skill_event call without touching the real sink."""
    events: list[dict[str, Any]] = []

    def _capture(
        skill_name,
        session_id,
        duration_ms,
        errored,
        server_version,
        diagnostic=None,
        error_class=None,
    ):
        events.append({"skill_name": skill_name, "diagnostic": diagnostic})

    monkeypatch.setattr(telemetry, "record_skill_event", _capture)
    return events


async def test_g2_dropped_dev_process_is_accepted_and_round_trips(captured_events):
    """A bicameral-ingest skill_end carrying g2_dropped_dev_process is recorded,
    not echoed back as an unknown field."""
    result = await _handle_skill_end_impl(
        session_id="sess-393",
        skill_name="bicameral-ingest",
        server_version="test",
        diagnostic={
            "decisions_ingested": 2,
            "g2_candidates_evaluated": 7,
            "g2_dropped_hard_exclude": 1,
            "g2_dropped_dev_process": 5,
        },
    )

    # Observable: the field was accepted — no warning surfaced to the LLM.
    assert result.get("diagnostic_warning") is None
    assert result["status"] == "recorded"

    # Observable: the value round-tripped into what was handed to the sink.
    assert len(captured_events) == 1
    recorded = captured_events[0]["diagnostic"]
    assert recorded["g2_dropped_dev_process"] == 5
    assert recorded["g2_dropped_hard_exclude"] == 1


async def test_unknown_field_still_warns_but_dev_process_survives(captured_events):
    """Negative control: a genuinely unknown field IS echoed as a warning and
    stripped from the recorded diagnostic, while g2_dropped_dev_process — now a
    declared field — survives in the same payload.

    If g2_dropped_dev_process were NOT declared on IngestDiagnostic, it would
    appear in diagnostic_warning here and this assertion would fail — that is
    what makes the positive test above non-vacuous.
    """
    result = await _handle_skill_end_impl(
        session_id="sess-393-neg",
        skill_name="bicameral-ingest",
        server_version="test",
        diagnostic={
            "g2_dropped_dev_process": 3,
            "g2_bogus_not_a_real_field": 1,
        },
    )

    warning = result.get("diagnostic_warning")
    assert warning is not None
    assert "g2_bogus_not_a_real_field" in warning
    assert "g2_dropped_dev_process" not in warning

    recorded = captured_events[0]["diagnostic"]
    assert recorded["g2_dropped_dev_process"] == 3
    assert "g2_bogus_not_a_real_field" not in recorded
