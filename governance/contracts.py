"""Governance contracts — Pydantic models for the deterministic
escalation policy engine.

Phase 1 (#109): ``GovernanceMetadata`` + ``derive_governance_metadata``
helper that maps the existing L1/L2/L3 ``decision_level`` axis to
sensible (decision_class, risk_class, escalation_class) defaults.

Phase 2 (#110): ``GovernanceFinding`` + ``GovernancePolicyResult`` —
the consolidation wrapper and the engine's output type. The Finding
carries an optional ``policy_result`` populated by Phase 3's engine.

Phase 4 (#112) will extend this module with ``HITLPrompt`` /
``HITLPromptOption`` for the bypassable preflight clarification flow.
That ships separately.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# ── Phase 1: GovernanceMetadata ──────────────────────────────────────


class GovernanceMetadata(BaseModel):
    """Per-decision governance classification.

    Orthogonal to the existing ``decision_level`` (L1/L2/L3) axis:
    L1/L2/L3 captures CodeGenome identity-write semantics, while
    GovernanceMetadata captures escalation visibility. The two
    coexist; ``derive_governance_metadata`` provides a sensible
    default mapping when explicit metadata is absent.
    """

    decision_class: Literal[
        "product_behavior",
        "architecture",
        "security",
        "compliance",
        "data_contract",
        "operational_reliability",
        "implementation_preference",
        "experimental",
    ] = "product_behavior"
    risk_class: Literal["low", "medium", "high", "critical"] = "medium"
    escalation_class: Literal[
        "context_only",
        "warn",
        "escalate",
        "notify_supervisor_allowed",
        "system_wide_warning_allowed",
    ] = "warn"
    owner: str | None = None
    supervisor: str | None = None
    notification_channels: list[str] = []
    protected_component: bool = False
    review_after: str | None = None


# Default mapping from decision_level (L1/L2/L3 or None) to a 3-tuple of
# (decision_class, risk_class, escalation_class). Used when explicit
# governance metadata isn't supplied — see ``derive_governance_metadata``.
_L1L2L3_DEFAULTS: dict[str | None, tuple[str, str, str]] = {
    "L1": ("product_behavior", "medium", "warn"),
    "L2": ("architecture", "medium", "escalate"),
    "L3": ("implementation_preference", "low", "context_only"),
    None: ("product_behavior", "medium", "warn"),
}


def derive_governance_metadata(
    decision_level: str | None,
    explicit: GovernanceMetadata | None,
) -> GovernanceMetadata:
    """Resolve effective governance metadata for a decision.

    Explicit metadata wins; otherwise derive from ``decision_level``
    using the L1/L2/L3 default table. Unknown levels (or ``None``)
    fall back to L1 defaults.
    """
    if explicit is not None:
        return explicit
    dc, rc, ec = _L1L2L3_DEFAULTS.get(decision_level, _L1L2L3_DEFAULTS[None])
    return GovernanceMetadata(
        decision_class=dc,  # type: ignore[arg-type]
        risk_class=rc,  # type: ignore[arg-type]
        escalation_class=ec,  # type: ignore[arg-type]
    )


# ── Phase 2: GovernanceFinding + GovernancePolicyResult ─────────────


class GovernancePolicyResult(BaseModel):
    """Output of the deterministic escalation evaluator (Phase 3).

    ``action`` is the visibility action selected by the policy engine
    after applying class defaults, semantic-status severity bumps,
    bypass downgrades, and the ``max_native_action`` ceiling. The
    engine is non-blocking by design: ``config.allow_blocking`` is
    locked at ``Literal[False]`` so no action is ever a merge block.
    """

    action: Literal[
        "ignore",
        "context",
        "warn",
        "escalate",
        "notify_supervisor",
        "system_wide_warning",
    ]
    gate: str
    reason: str
    matched_conditions: list[str] = []
    missing_conditions: list[str] = []
    evidence_refs: list[str] = []
    suggested_recipients: list[str] = []
    requires_human_resolution: bool = False


class GovernanceFinding(BaseModel):
    """Consolidated finding wrapper that the engine evaluates.

    A finding represents one (decision_id, region_id) pair plus the
    semantic status of whatever change was observed. Findings can come
    from compliance verdicts, drift entries, preflight drift candidates,
    or LLM judges; ``finding_factories`` provides the builders. The
    ``policy_result`` field is populated by ``engine.evaluate`` after
    construction.
    """

    finding_id: str  # UUIDv4
    decision_id: str
    region_id: str | None = None

    decision_class: str | None = None
    risk_class: str | None = None
    escalation_class: str | None = None

    source: Literal[
        "preflight",
        "drift",
        "resolve_compliance",
        "link_commit",
        "scan_branch",
        "llm_judge",
    ]

    semantic_status: Literal[
        "not_relevant",
        "cosmetic_change",
        "behavior_preserving_refactor",
        "possible_drift",
        "likely_drift",
        "confirmed_drift",
        "critical_drift",
        "supersession_candidate",
        "binding_uncertain",
        "needs_human_review",
    ]

    confidence: dict[str, float | str] = {}
    explanation: str
    evidence_refs: list[str] = []
    policy_result: GovernancePolicyResult | None = None


# ── Phase 4: HITL prompt + option (#112) ─────────────────────────────


class HITLPromptOption(BaseModel):
    """One selectable option in a preflight HITL clarification prompt.

    The ``kind`` enum is closed; the skill side renders ``label`` to
    the user and routes the chosen kind back to the appropriate
    follow-up tool. ``bypass`` is mandatory and must always be the
    final option (skill-side assertion enforces ordering).
    """

    kind: Literal[
        "ratify",
        "reject",
        "needs_context",
        "defer",
        "bypass",
        "supersedes_a_b",
        "supersedes_b_a",
        "keep_parallel",
        "confirm_proposed",
        "ratify_now",
    ]
    label: str


class HITLPrompt(BaseModel):
    """A preflight clarification prompt the agent should surface via
    AskUserQuestion when a decision has an unresolved signoff state.

    Bypass is mandatory and enforced as the LAST option in
    ``options`` -- the skill assertion fails otherwise. Bypass writes
    a ``preflight_prompt_bypassed`` event via ``preflight_telemetry``
    but does NOT mutate decision state; the unresolved status persists
    for future preflight surfaces. Recently-bypassed decisions are
    treated one tier softer by the engine.
    """

    decision_id: str
    trigger: Literal[
        "proposed",
        "ai_surfaced",
        "needs_context",
        "collision_pending",
        "context_pending",
    ]
    question: str
    options: list[HITLPromptOption]
