"""Phase 4 (#112) — preflight HITL prompt emission unit tests.

These tests exercise the pure HITL builder helpers in
``handlers.preflight``. They do not boot a ledger; they hand the
builder synthesised ``DecisionMatch`` / ``BriefDecision`` rows and
verify the emitted prompts.

The bypass-option-mandatory-and-last contract is tested across every
trigger shape — that's the skill-side assertion contract, so we lock
it down at the type/shape level.
"""

from __future__ import annotations

from contracts import BriefDecision, CodeRegionSummary, DecisionMatch
from handlers.preflight import _build_hitl_prompts


def _match(decision_id: str, signoff_state: str, description: str = "") -> DecisionMatch:
    """Helper: build a minimal DecisionMatch for HITL prompt scanning."""
    return DecisionMatch(
        decision_id=decision_id,
        description=description or f"decision text for {decision_id}",
        status="pending",
        signoff_state=signoff_state,
        confidence=0.9,
        source_ref="ref-1",
        code_regions=[CodeRegionSummary(file_path="x.py", symbol="f", lines=(1, 5), purpose="t")],
    )


def _brief(decision_id: str, description: str = "") -> BriefDecision:
    return BriefDecision(
        decision_id=decision_id,
        description=description or f"brief text for {decision_id}",
        status="pending",
    )


def test_proposed_decision_triggers_prompt() -> None:
    """A proposed signoff_state on a region match emits the generic prompt."""
    prompts = _build_hitl_prompts(
        region_matches=[_match("dec-1", "proposed", "Adopt Stripe webhook idempotency")],
        unresolved_collisions=[],
        context_pending_ready=[],
    )
    assert len(prompts) == 1
    p = prompts[0]
    assert p.decision_id == "dec-1"
    assert p.trigger == "proposed"
    kinds = [opt.kind for opt in p.options]
    assert kinds == ["ratify", "reject", "needs_context", "defer", "bypass"]


def test_collision_pending_triggers_competing_prompt() -> None:
    """unresolved_collisions emits the competing-decisions option set."""
    prompts = _build_hitl_prompts(
        region_matches=[],
        unresolved_collisions=[_brief("dec-coll-A", "Use Redis for sessions")],
        context_pending_ready=[],
    )
    assert len(prompts) == 1
    p = prompts[0]
    assert p.trigger == "collision_pending"
    kinds = [opt.kind for opt in p.options]
    assert kinds == [
        "supersedes_a_b",
        "supersedes_b_a",
        "keep_parallel",
        "defer",
        "bypass",
    ]


def test_ai_surfaced_triggers_ai_surfaced_prompt() -> None:
    """ai_surfaced signoff_state emits the AI-surfaced option set."""
    prompts = _build_hitl_prompts(
        region_matches=[_match("dec-ai", "ai_surfaced", "Use bcrypt for password hashing")],
        unresolved_collisions=[],
        context_pending_ready=[],
    )
    assert len(prompts) == 1
    p = prompts[0]
    assert p.trigger == "ai_surfaced"
    kinds = [opt.kind for opt in p.options]
    assert kinds == [
        "confirm_proposed",
        "ratify_now",
        "reject",
        "needs_context",
        "bypass",
    ]


def test_ratified_decision_does_not_trigger_prompt() -> None:
    """A ratified signoff_state is resolved → no HITL prompt."""
    prompts = _build_hitl_prompts(
        region_matches=[_match("dec-r", "ratified")],
        unresolved_collisions=[],
        context_pending_ready=[],
    )
    assert prompts == []


def test_every_prompt_includes_bypass_option_last() -> None:
    """Skill-side contract: bypass option is mandatory and last."""
    prompts = _build_hitl_prompts(
        region_matches=[
            _match("dec-1", "proposed"),
            _match("dec-2", "ai_surfaced"),
            _match("dec-3", "needs_context"),
        ],
        unresolved_collisions=[_brief("dec-4")],
        context_pending_ready=[_brief("dec-5")],
    )
    # 5 unique decisions × 1 prompt each.
    assert len(prompts) == 5
    for p in prompts:
        assert len(p.options) >= 2  # at least one real option + bypass
        assert p.options[-1].kind == "bypass", (
            f"prompt for {p.decision_id} ({p.trigger}) does not end with bypass option"
        )


def test_prompt_preserves_decision_id_for_audit() -> None:
    """The decision_id round-trips into the prompt for skill-side dispatch."""
    prompts = _build_hitl_prompts(
        region_matches=[_match("dec:abc123", "proposed", "test description")],
        unresolved_collisions=[],
        context_pending_ready=[],
    )
    assert len(prompts) == 1
    assert prompts[0].decision_id == "dec:abc123"
    # Question contains a snippet of the description for context.
    assert "test description" in prompts[0].question or "..." in prompts[0].question
