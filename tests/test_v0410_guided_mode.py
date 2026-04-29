"""v0.4.10 — guided mode action hint regression tests.

Three layers:

  1. **Pure-function tests** for the hint generators. Every hint kind
     (review_drift, ground_decision, resolve_divergence,
     answer_open_questions) is exercised in BOTH modes (advisory =
     blocking False, guided = blocking True) to lock in the v0.4.10
     semantic: hints fire whenever findings exist, regardless of
     guided_mode. The flag controls intensity, not existence.

  2. **Context flag test** — `BICAMERAL_GUIDED_MODE` env var parses
     correctly into `BicameralContext.guided_mode`, plus the config
     file fallback (`.bicameral/config.yaml`).

  3. **Backward compat** — when there are no findings, both modes
     return empty `action_hints`. When there are findings,
     `action_hints` is never empty in either mode.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from contracts import (
    ActionHint,
    BriefDecision,
    BriefDivergence,
    BriefGap,
    CodeRegionSummary,
    DecisionMatch,
    LinkCommitResponse,
    SearchDecisionsResponse,
)
from handlers.action_hints import (
    generate_hints_for_search,
    generate_hints_from_findings,
)

# ── Helper factories ────────────────────────────────────────────────


def _match(
    *,
    intent_id: str = "decision:1",
    description: str = "test decision",
    status: str = "reflected",
    file_path: str = "src/foo.ts",
    symbol: str = "foo",
) -> DecisionMatch:
    return DecisionMatch(
        decision_id=intent_id,
        description=description,
        status=status,  # type: ignore[arg-type]
        confidence=0.9,
        source_ref="test-ref",
        code_regions=[
            CodeRegionSummary(
                file_path=file_path,
                symbol=symbol,
                lines=(10, 30),
                purpose="",
            )
        ],
    )


def _search_response(matches: list[DecisionMatch]) -> SearchDecisionsResponse:
    return SearchDecisionsResponse(
        query="test",
        sync_status=LinkCommitResponse(
            commit_hash="abc123",
            synced=True,
            reason="new_commit",
            regions_updated=0,
            decisions_reflected=0,
            decisions_drifted=0,
            undocumented_symbols=[],
        ),
        matches=matches,
        ungrounded_count=sum(1 for m in matches if m.status == "ungrounded"),
        suggested_review=[m.decision_id for m in matches if m.status in ("drifted", "pending")],
    )


def _brief_decision(
    intent_id: str = "decision:1",
    description: str = "test",
    status: str = "reflected",
) -> BriefDecision:
    return BriefDecision(
        decision_id=intent_id,
        description=description,
        status=status,  # type: ignore[arg-type]
        source_ref="test-ref",
        code_regions=[],
        severity_tier=1,
    )


# ── Search hint generator ──────────────────────────────────────────


def test_search_no_findings_no_hints_in_either_mode():
    response = _search_response([_match(status="reflected")])
    assert generate_hints_for_search(response, guided_mode=False) == []
    assert generate_hints_for_search(response, guided_mode=True) == []


def test_search_empty_matches_no_hints_in_either_mode():
    response = _search_response([])
    assert generate_hints_for_search(response, guided_mode=False) == []
    assert generate_hints_for_search(response, guided_mode=True) == []


def test_search_drifted_match_fires_in_normal_mode_as_advisory():
    """v0.4.10: hints fire even in normal mode, just non-blocking."""
    response = _search_response(
        [
            _match(intent_id="decision:1", status="drifted", file_path="src/a.ts"),
        ]
    )
    hints = generate_hints_for_search(response, guided_mode=False)
    assert len(hints) == 1
    h = hints[0]
    assert h.kind == "review_drift"
    assert h.blocking is False
    # Advisory tone — soft language
    assert "Heads up" in h.message or "look drifted" in h.message
    assert "decision:1" in h.refs
    assert "src/a.ts" in h.refs


def test_search_drifted_match_fires_in_guided_mode_as_blocking():
    response = _search_response(
        [
            _match(intent_id="decision:1", status="drifted", file_path="src/a.ts"),
            _match(intent_id="decision:2", status="drifted", file_path="src/b.ts"),
        ]
    )
    hints = generate_hints_for_search(response, guided_mode=True)
    review = [h for h in hints if h.kind == "review_drift"]
    assert len(review) == 1
    h = review[0]
    assert h.blocking is True
    # Imperative tone — strict language
    assert "BEFORE" in h.message
    assert "2 matched decision(s) have drifted" in h.message
    assert "decision:1" in h.refs
    assert "decision:2" in h.refs
    assert "src/a.ts" in h.refs
    assert "src/b.ts" in h.refs


def test_search_ungrounded_fires_in_both_modes():
    response = _search_response(
        [
            _match(intent_id="decision:1", status="ungrounded"),
        ]
    )
    response.matches[0].code_regions = []

    advisory = generate_hints_for_search(response, guided_mode=False)
    blocking = generate_hints_for_search(response, guided_mode=True)

    assert len(advisory) == 1
    assert advisory[0].kind == "ground_decision"
    assert advisory[0].blocking is False

    assert len(blocking) == 1
    assert blocking[0].kind == "ground_decision"
    assert blocking[0].blocking is True


def test_search_message_tone_differs_between_modes():
    """The `message` field uses different wording in each mode so users
    can tell at a glance whether they're being advised or required."""
    response = _search_response([_match(status="drifted")])
    advisory = generate_hints_for_search(response, guided_mode=False)[0]
    blocking = generate_hints_for_search(response, guided_mode=True)[0]
    assert advisory.message != blocking.message


def test_search_fires_both_review_and_ground_when_mixed():
    response = _search_response(
        [
            _match(intent_id="decision:1", status="drifted"),
            _match(intent_id="decision:2", status="ungrounded"),
            _match(intent_id="decision:3", status="reflected"),
        ]
    )
    for guided in (False, True):
        hints = generate_hints_for_search(response, guided_mode=guided)
        kinds = {h.kind for h in hints}
        assert "review_drift" in kinds
        assert "ground_decision" in kinds
        # Both should agree on blocking value
        assert all(h.blocking == guided for h in hints)


# ── Findings hint generator (replaces brief hint generator) ────────


def test_findings_empty_no_hints_in_either_mode():
    assert generate_hints_from_findings([], [], [], guided_mode=False) == []
    assert generate_hints_from_findings([], [], [], guided_mode=True) == []


def test_findings_divergence_fires_in_normal_mode_as_advisory():
    divergence = BriefDivergence(
        symbol="SessionCache",
        file_path="src/session.ts",
        conflicting_decisions=[
            _brief_decision(intent_id="a", description="Use Redis"),
            _brief_decision(intent_id="b", description="Use local memory"),
        ],
        summary="2 decisions contradict",
    )
    hints = generate_hints_from_findings([divergence], [], [], guided_mode=False)
    assert len(hints) == 1
    assert hints[0].kind == "resolve_divergence"
    assert hints[0].blocking is False
    assert any("SessionCache" in ref for ref in hints[0].refs)


def test_findings_divergence_fires_in_guided_mode_as_blocking():
    divergence = BriefDivergence(
        symbol="X",
        file_path="src/x.ts",
        conflicting_decisions=[_brief_decision(), _brief_decision()],
        summary="conflict",
    )
    hints = generate_hints_from_findings([divergence], [], [], guided_mode=True)
    assert len(hints) == 1
    assert hints[0].blocking is True
    assert "BEFORE" in hints[0].message


def test_findings_drift_candidates_fire_in_both_modes():
    drift = [
        _brief_decision(intent_id="a", status="drifted"),
        _brief_decision(intent_id="b", status="drifted"),
    ]
    advisory = generate_hints_from_findings([], drift, [], guided_mode=False)
    blocking = generate_hints_from_findings([], drift, [], guided_mode=True)

    assert len(advisory) == 1 and advisory[0].kind == "review_drift"
    assert advisory[0].blocking is False
    assert advisory[0].refs == ["a", "b"]

    assert len(blocking) == 1 and blocking[0].kind == "review_drift"
    assert blocking[0].blocking is True
    assert blocking[0].refs == ["a", "b"]


def test_findings_open_question_gap_fires_in_both_modes():
    gaps = [
        BriefGap(
            description="RSVP sync direction — bidirectional or one-way?",
            hint="open-question phrasing (vs/or/tbd/?)",
        ),
        BriefGap(
            description="unrelated gap",
            hint="missing acceptance criteria",
        ),
    ]
    for guided in (False, True):
        hints = generate_hints_from_findings([], [], gaps, guided_mode=guided)
        open_q = [h for h in hints if h.kind == "answer_open_questions"]
        assert len(open_q) == 1
        assert open_q[0].blocking is guided
        assert len(open_q[0].refs) == 1
        assert "RSVP sync" in open_q[0].refs[0]


def test_findings_fires_all_three_kinds_when_everything_present():
    drift = [_brief_decision(intent_id="a", status="drifted")]
    divergences = [
        BriefDivergence(
            symbol="X",
            file_path="src/x.ts",
            conflicting_decisions=[_brief_decision(), _brief_decision()],
            summary="conflict",
        )
    ]
    gaps = [BriefGap(description="open q", hint="open-question phrasing")]
    for guided in (False, True):
        hints = generate_hints_from_findings(divergences, drift, gaps, guided_mode=guided)
        kinds = {h.kind for h in hints}
        assert kinds == {"resolve_divergence", "review_drift", "answer_open_questions"}
        assert all(h.blocking == guided for h in hints)


# ── Backward compat ─────────────────────────────────────────────────


def test_action_hints_default_to_empty_list():
    """Default constructor produces empty action_hints."""
    response = _search_response([_match()])
    assert response.action_hints == []


# ── Context flag parsing ────────────────────────────────────────────


@pytest.mark.parametrize(
    "env_val,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("maybe", False),  # unrecognized → falls through to config file → false
    ],
)
def test_guided_mode_env_truthy_set(env_val: str, expected: bool):
    """Truthy/falsy env values map correctly via the helper sets."""
    from context import _GUIDED_MODE_FALSY, _GUIDED_MODE_TRUTHY

    is_truthy = env_val.strip().lower() in _GUIDED_MODE_TRUTHY
    if expected:
        assert is_truthy
    else:
        # false/no/off should be in the FALSY set; "maybe" / unknowns go
        # to neither and fall through to config file in _read_guided_mode
        assert not is_truthy


def test_read_guided_mode_falls_back_to_false_when_no_config(tmp_path, monkeypatch):
    monkeypatch.delenv("BICAMERAL_GUIDED_MODE", raising=False)
    from context import _read_guided_mode

    assert _read_guided_mode(str(tmp_path)) is False


def test_read_guided_mode_reads_config_yaml_true(tmp_path, monkeypatch):
    monkeypatch.delenv("BICAMERAL_GUIDED_MODE", raising=False)
    cfg_dir = tmp_path / ".bicameral"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("mode: solo\nguided: true\n")
    from context import _read_guided_mode

    assert _read_guided_mode(str(tmp_path)) is True


def test_read_guided_mode_reads_config_yaml_false(tmp_path, monkeypatch):
    monkeypatch.delenv("BICAMERAL_GUIDED_MODE", raising=False)
    cfg_dir = tmp_path / ".bicameral"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("mode: solo\nguided: false\n")
    from context import _read_guided_mode

    assert _read_guided_mode(str(tmp_path)) is False


def test_env_var_overrides_config_file(tmp_path, monkeypatch):
    """BICAMERAL_GUIDED_MODE=1 wins even when config.yaml says false."""
    cfg_dir = tmp_path / ".bicameral"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("mode: solo\nguided: false\n")
    monkeypatch.setenv("BICAMERAL_GUIDED_MODE", "1")
    from context import _read_guided_mode

    assert _read_guided_mode(str(tmp_path)) is True


def test_env_var_can_force_off_against_config_file(tmp_path, monkeypatch):
    """BICAMERAL_GUIDED_MODE=0 wins even when config.yaml says true."""
    cfg_dir = tmp_path / ".bicameral"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text("mode: solo\nguided: true\n")
    monkeypatch.setenv("BICAMERAL_GUIDED_MODE", "0")
    from context import _read_guided_mode

    assert _read_guided_mode(str(tmp_path)) is False
