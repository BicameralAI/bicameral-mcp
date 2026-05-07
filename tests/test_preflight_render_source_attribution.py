"""Behavioral tests for `handlers.preflight._apply_attribution_policy`
(#200 Phase 3).

The policy gates how `source_ref` lines on surfaced decisions render
to the agent / chat. Three modes from `.bicameral/config.yaml:
render_source_attribution`:
  - `full`: pass through verbatim (legacy)
  - `redacted` (default): replace name + date patterns with placeholders
  - `hidden`: blank the source_ref entirely

The policy is server-side: the agent receives pre-filtered decisions
and renders whatever it gets. Skill text just says "render `source_ref`
verbatim from the server response" — the deterministic gate is the
config-load-time policy choice.
"""

from __future__ import annotations

from contracts import DecisionMatch
from handlers.preflight import _apply_attribution_policy


def _make_match(source_ref: str) -> DecisionMatch:
    return DecisionMatch(
        decision_id="d-1",
        description="test decision",
        status="reflected",
        signoff_state="ratified",
        confidence=0.9,
        source_ref=source_ref,
        code_regions=[],
    )


def test_full_mode_passes_through_verbatim() -> None:
    matches = [
        _make_match("Brian 2026-03-22"),
        _make_match("Sprint 14 architecture review · Ian, 2026-03-12"),
    ]
    result = _apply_attribution_policy(matches, mode="full")
    assert result[0].source_ref == "Brian 2026-03-22"
    assert result[1].source_ref == "Sprint 14 architecture review · Ian, 2026-03-12"


def test_redacted_mode_replaces_name_and_date_patterns() -> None:
    # #209 refinement: name redaction now requires a positional cue
    # (`· `, `, ` adjacent to a date, `Speaker:`, `From:`). Bare names
    # like "Brian 2026-03-22" without any cue pass through unchanged
    # (modulo the date, which is unambiguous and doesn't need a cue).
    # Use the canonical attribution shape so the name redaction fires.
    matches = [_make_match("Sprint review · Brian, 2026-03-22")]
    result = _apply_attribution_policy(matches, mode="redacted")
    assert "Brian" not in result[0].source_ref
    assert "2026-03-22" not in result[0].source_ref
    assert "<NAME_REDACTED>" in result[0].source_ref
    assert "<DATE_REDACTED>" in result[0].source_ref


def test_hidden_mode_strips_source_ref_field_entirely() -> None:
    matches = [_make_match("Brian 2026-03-22")]
    result = _apply_attribution_policy(matches, mode="hidden")
    assert result[0].source_ref == ""
