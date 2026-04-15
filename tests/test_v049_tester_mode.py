"""v0.4.9 Phase 2 — tester mode action hint regression tests.

Two layers:

  1. **Pure-function tests** for the hint generators
     (``generate_hints_for_search`` + ``generate_hints_for_brief``).
     These exercise every hint kind: review_drift, ground_decision,
     resolve_divergence, answer_open_questions. Synchronous, IO-free.

  2. **Context flag test** — verify ``BICAMERAL_TESTER_MODE`` env var
     parses correctly into ``BicameralContext.tester_mode``.

  3. **Backward-compat test** — when ``tester_mode=False`` (default),
     responses must be byte-identical to v0.4.8 except for the new
     empty ``action_hints=[]`` field. No hint should ever fire.
"""

from __future__ import annotations

import os

import pytest

from contracts import (
    ActionHint,
    BriefDecision,
    BriefDivergence,
    BriefGap,
    BriefResponse,
    CodeRegionSummary,
    DecisionMatch,
    LinkCommitResponse,
    SearchDecisionsResponse,
)
from handlers.action_hints import (
    generate_hints_for_brief,
    generate_hints_for_search,
)


# ── Helper factories ────────────────────────────────────────────────


def _match(
    *,
    intent_id: str = "intent:1",
    description: str = "test decision",
    status: str = "reflected",
    file_path: str = "src/foo.ts",
    symbol: str = "foo",
) -> DecisionMatch:
    return DecisionMatch(
        intent_id=intent_id,
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
        suggested_review=[m.intent_id for m in matches if m.status in ("drifted", "pending")],
    )


def _brief_decision(
    intent_id: str = "intent:1",
    description: str = "test",
    status: str = "reflected",
) -> BriefDecision:
    return BriefDecision(
        intent_id=intent_id,
        description=description,
        status=status,  # type: ignore[arg-type]
        source_ref="test-ref",
        code_regions=[],
        severity_tier=1,
    )


def _brief_response(
    *,
    decisions: list[BriefDecision] | None = None,
    drift_candidates: list[BriefDecision] | None = None,
    divergences: list[BriefDivergence] | None = None,
    gaps: list[BriefGap] | None = None,
) -> BriefResponse:
    return BriefResponse(
        topic="test",
        as_of="2026-04-14T00:00:00Z",
        ref="HEAD",
        decisions=decisions or [],
        drift_candidates=drift_candidates or [],
        divergences=divergences or [],
        gaps=gaps or [],
        suggested_questions=[],
    )


# ── generate_hints_for_search ──────────────────────────────────────


def test_search_no_hints_when_tester_mode_off():
    response = _search_response([
        _match(status="drifted"),
        _match(status="ungrounded"),
    ])
    hints = generate_hints_for_search(response, tester_mode=False)
    assert hints == []


def test_search_empty_matches_produces_no_hints():
    response = _search_response([])
    hints = generate_hints_for_search(response, tester_mode=True)
    assert hints == []


def test_search_drifted_match_fires_review_drift():
    response = _search_response([
        _match(intent_id="intent:1", status="drifted", file_path="src/a.ts"),
        _match(intent_id="intent:2", status="drifted", file_path="src/b.ts"),
        _match(intent_id="intent:3", status="reflected"),
    ])
    hints = generate_hints_for_search(response, tester_mode=True)
    review = [h for h in hints if h.kind == "review_drift"]
    assert len(review) == 1
    hint = review[0]
    assert hint.blocking is True
    assert "2 matched decision(s) have drifted" in hint.message
    # Refs include the drifted intent_ids and the files they touch
    assert "intent:1" in hint.refs
    assert "intent:2" in hint.refs
    assert "src/a.ts" in hint.refs
    assert "src/b.ts" in hint.refs


def test_search_ungrounded_match_fires_ground_decision():
    response = _search_response([
        _match(intent_id="intent:1", status="ungrounded"),
    ])
    # Ungrounded match — DecisionMatch still needs at least one region for
    # the helper. Fabricate one with "ungrounded" semantic directly.
    response.matches[0].code_regions = []
    hints = generate_hints_for_search(response, tester_mode=True)
    ground = [h for h in hints if h.kind == "ground_decision"]
    assert len(ground) == 1
    assert ground[0].blocking is True
    assert "intent:1" in ground[0].refs


def test_search_fires_both_review_and_ground_when_mixed():
    response = _search_response([
        _match(intent_id="intent:1", status="drifted"),
        _match(intent_id="intent:2", status="ungrounded"),
        _match(intent_id="intent:3", status="reflected"),
    ])
    hints = generate_hints_for_search(response, tester_mode=True)
    kinds = {h.kind for h in hints}
    assert "review_drift" in kinds
    assert "ground_decision" in kinds


def test_search_all_reflected_fires_no_hints():
    response = _search_response([
        _match(intent_id="intent:1", status="reflected"),
        _match(intent_id="intent:2", status="reflected"),
    ])
    hints = generate_hints_for_search(response, tester_mode=True)
    assert hints == []


# ── generate_hints_for_brief ───────────────────────────────────────


def test_brief_no_hints_when_tester_mode_off():
    response = _brief_response(
        drift_candidates=[_brief_decision(status="drifted")],
    )
    hints = generate_hints_for_brief(response, tester_mode=False)
    assert hints == []


def test_brief_divergence_fires_resolve_divergence():
    divergence = BriefDivergence(
        symbol="SessionCache",
        file_path="src/session.ts",
        conflicting_decisions=[
            _brief_decision(intent_id="a", description="Use Redis"),
            _brief_decision(intent_id="b", description="Use local memory"),
        ],
        summary="2 decisions contradict",
    )
    response = _brief_response(divergences=[divergence])
    hints = generate_hints_for_brief(response, tester_mode=True)
    divergent = [h for h in hints if h.kind == "resolve_divergence"]
    assert len(divergent) == 1
    h = divergent[0]
    assert h.blocking is True
    assert "1 divergent decision pair(s)" in h.message
    assert any("SessionCache" in ref for ref in h.refs)


def test_brief_drift_candidates_fire_review_drift():
    response = _brief_response(
        drift_candidates=[
            _brief_decision(intent_id="a", status="drifted"),
            _brief_decision(intent_id="b", status="drifted"),
        ],
    )
    hints = generate_hints_for_brief(response, tester_mode=True)
    review = [h for h in hints if h.kind == "review_drift"]
    assert len(review) == 1
    assert "2 decision(s) in scope have drifted" in review[0].message
    assert review[0].refs == ["a", "b"]


def test_brief_open_question_gap_fires_answer_open_questions():
    response = _brief_response(
        gaps=[
            BriefGap(
                description="RSVP sync direction — bidirectional or one-way?",
                hint="open-question phrasing (vs/or/tbd/?)",
            ),
            BriefGap(
                description="unrelated gap",
                hint="missing acceptance criteria",  # not open-question
            ),
        ],
    )
    hints = generate_hints_for_brief(response, tester_mode=True)
    open_q = [h for h in hints if h.kind == "answer_open_questions"]
    assert len(open_q) == 1
    assert "1 unanswered open question(s)" in open_q[0].message
    # Only the open-question gap's description is in refs, not the other
    assert len(open_q[0].refs) == 1
    assert "RSVP sync" in open_q[0].refs[0]


def test_brief_fires_all_three_hint_kinds_when_everything_present():
    response = _brief_response(
        drift_candidates=[_brief_decision(intent_id="a", status="drifted")],
        divergences=[
            BriefDivergence(
                symbol="X",
                file_path="src/x.ts",
                conflicting_decisions=[_brief_decision(), _brief_decision()],
                summary="conflict",
            )
        ],
        gaps=[
            BriefGap(
                description="open q",
                hint="open-question phrasing",
            ),
        ],
    )
    hints = generate_hints_for_brief(response, tester_mode=True)
    kinds = {h.kind for h in hints}
    assert kinds == {"resolve_divergence", "review_drift", "answer_open_questions"}
    # All three should be blocking
    assert all(h.blocking for h in hints)


def test_brief_empty_response_produces_no_hints():
    response = _brief_response()
    hints = generate_hints_for_brief(response, tester_mode=True)
    assert hints == []


# ── Backward compat ─────────────────────────────────────────────────


def test_action_hints_default_to_empty_list():
    """Backward compat: v0.4.8 callers that don't set action_hints must
    see an empty list, not None."""
    response = _search_response([_match()])
    assert response.action_hints == []

    brief = _brief_response()
    assert brief.action_hints == []


# ── Context flag parsing ────────────────────────────────────────────


@pytest.mark.parametrize("env_val,expected", [
    ("1", True),
    ("true", True),
    ("True", True),
    ("TRUE", True),
    ("yes", True),
    ("on", True),
    ("0", False),
    ("false", False),
    ("", False),
    ("no", False),
    ("maybe", False),
])
def test_tester_mode_env_parse(env_val: str, expected: bool, monkeypatch):
    """BICAMERAL_TESTER_MODE accepts a specific set of truthy values."""
    from context import _TESTER_MODE_TRUTHY
    # The env-parse logic lives in BicameralContext.from_env(). Testing
    # the helper set directly here instead of round-tripping through
    # from_env() to avoid needing a real ledger/code_graph.
    assert (env_val.strip().lower() in _TESTER_MODE_TRUTHY) == expected
