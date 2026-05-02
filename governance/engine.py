"""Deterministic escalation policy engine.

The engine is a **pure function** over four inputs:

  1. A ``GovernanceFinding`` describing what was observed.
  2. The decision's ``GovernanceMetadata`` (or its L1/L2/L3-derived
     defaults — see ``governance.contracts.derive_governance_metadata``).
  3. The user's ``GovernanceConfig`` parsed from
     ``.bicameral/governance.yml``.
  4. The current ``decision_status`` (signoff state) and an optional
     ``bypass_recency_seconds`` scalar.

No IO, no clock, no state. Bypass recency is a **scalar parameter**
computed at the call site (preflight handler reads
``preflight_telemetry.recent_bypass_seconds`` in Phase 4 once the
HITL flow lands; until then preflight passes ``None``).

The orchestrator ``evaluate()`` is ~15 LOC of straight-line composition
over four bounded helpers — each helper is independently unit-testable.
"""

from __future__ import annotations

from typing import Literal

from governance.config import GovernanceConfig
from governance.contracts import (
    GovernanceFinding,
    GovernanceMetadata,
    GovernancePolicyResult,
)

# Decision lifecycle signoff states the engine reads. Mirrors the
# values used in handlers/decision_status.py and the signoff field of
# the decision schema. ``ratified`` and ``active`` are the "good"
# states for supervisor notification; the rest indicate the decision
# is not yet live or has been superseded.
DecisionStatus = Literal[
    "ratified",
    "proposed",
    "rejected",
    "superseded",
    "active",
    "ungrounded",
    "context_pending",
    "collision_pending",
]


# Action ladder: index = severity. Used for ceiling enforcement and
# bypass-induced tier downgrades.
_ACTION_LADDER: tuple[str, ...] = (
    "ignore",
    "context",
    "warn",
    "escalate",
    "notify_supervisor",
    "system_wide_warning",
)

_BYPASS_RECENCY_WINDOW_SECONDS = 3600  # 1 hour

# Severity ordering for the semantic_status enum on findings — used
# by ``_apply_class_defaults`` to bump the per-class default action
# when the observed semantic status warrants it. Mirrors the order in
# ``finding_factories._SEMANTIC_SEVERITY`` but kept private here so
# the engine isn't coupled to the factory module's internals.
_SEMANTIC_RANK: dict[str, int] = {
    "not_relevant": 0,
    "cosmetic_change": 1,
    "behavior_preserving_refactor": 1,
    "binding_uncertain": 2,
    "supersession_candidate": 2,
    "needs_human_review": 3,
    "possible_drift": 3,
    "likely_drift": 4,
    "confirmed_drift": 5,
    "critical_drift": 6,
}


def evaluate(
    finding: GovernanceFinding,
    metadata: GovernanceMetadata,
    config: GovernanceConfig,
    decision_status: DecisionStatus,
    bypass_recency_seconds: int | None,
) -> GovernancePolicyResult:
    """Pure deterministic orchestrator. Composes four helpers.

    ``bypass_recency_seconds`` is the elapsed seconds since the most
    recent bypass event for this decision, or ``None`` if no bypass
    is in the recency window. Phase 4's preflight handler computes
    this; Phase 3 callers pass ``None``.
    """
    matched, missing = _check_required_conditions(finding, metadata, config, decision_status)
    base_action = _apply_class_defaults(metadata, config, finding)
    after_bypass = _apply_bypass_downgrade(base_action, bypass_recency_seconds)
    final_action = _apply_max_native_ceiling(after_bypass, config)
    return GovernancePolicyResult(
        action=final_action,  # type: ignore[arg-type]
        gate=_gate_name_for(metadata),
        reason=_compose_reason(matched, missing, finding, metadata, final_action),
        matched_conditions=matched,
        missing_conditions=missing,
        evidence_refs=list(finding.evidence_refs),
        suggested_recipients=list(metadata.notification_channels),
        requires_human_resolution=(final_action in ("notify_supervisor", "system_wide_warning")),
    )


def _check_required_conditions(
    finding: GovernanceFinding,
    metadata: GovernanceMetadata,
    config: GovernanceConfig,
    decision_status: DecisionStatus,
) -> tuple[list[str], list[str]]:
    """Partition the required-conditions ladder into matched / missing.

    Each condition name maps to a deterministic predicate over the
    inputs. Anything we can't evaluate (e.g. an unknown condition
    string in a future-version config) is reported as missing so the
    audit trail makes the gap visible.
    """
    matched: list[str] = []
    missing: list[str] = []

    class_policy = config.decision_classes.get(metadata.decision_class)
    drift_threshold = (
        class_policy.supervisor_thresholds.get("drift_confidence", 0.0) if class_policy else 0.0
    )
    binding_threshold = (
        class_policy.supervisor_thresholds.get("binding_confidence", 0.0) if class_policy else 0.0
    )

    drift_conf = _confidence_value(finding.confidence.get("drift_confidence"))
    binding_conf = _confidence_value(finding.confidence.get("binding_confidence"))

    predicates: dict[str, bool] = {
        "decision_status_is_ratified": decision_status == "ratified",
        "decision_is_active": decision_status in ("ratified", "active"),
        "protected_decision_class": (
            metadata.protected_component or metadata.decision_class in ("security", "compliance")
        ),
        "no_superseding_decision": decision_status != "superseded",
        "drift_confidence_above_threshold": drift_conf >= drift_threshold,
        "binding_confidence_above_threshold": binding_conf >= binding_threshold,
    }

    for cond in config.required_conditions_for_supervisor_notification:
        if predicates.get(cond, False):
            matched.append(cond)
        else:
            missing.append(cond)
    return matched, missing


def _apply_class_defaults(
    metadata: GovernanceMetadata,
    config: GovernanceConfig,
    finding: GovernanceFinding,
) -> str:
    """Pick a base action from the class policy + semantic severity.

    Looks up the per-class default action; bumps it up the action
    ladder when the finding's semantic_status warrants more visibility
    (likely_drift bumps to escalate; confirmed/critical drift may bump
    further when the class policy permits supervisor notification).
    """
    class_policy = config.decision_classes.get(metadata.decision_class)
    rank = _SEMANTIC_RANK.get(finding.semantic_status, 0)

    # When no class policy is configured, the base action mirrors the
    # severity of the observed semantic_status directly. This lets a
    # vanilla config (no decision_classes block) still produce a
    # sensible action ladder: not_relevant → ignore, cosmetic → context,
    # possible/likely drift → warn/escalate, etc.
    if class_policy is None:
        if rank == 0:
            return "ignore"
        if rank == 1:
            return "context"
        if rank <= 3:
            return "warn"
        if rank == 4:
            return "escalate"
        return "escalate"  # confirmed/critical without class policy stays at escalate

    base = class_policy.default_action

    if rank == 0:
        return "ignore"
    if rank == 1:
        return _max_action(base, "context")
    if rank <= 3:
        return _max_action(base, "warn")
    if rank == 4:
        return _max_action(base, "escalate")
    # rank >= 5: confirmed_drift or critical_drift
    if class_policy.system_wide_warning_allowed:
        return _max_action(base, "system_wide_warning")
    if class_policy.supervisor_notification_allowed:
        return _max_action(base, "notify_supervisor")
    return _max_action(base, "escalate")


def _apply_bypass_downgrade(action: str, recency: int | None) -> str:
    """Drop one tier on the action ladder when a recent bypass exists.

    Recency is the elapsed seconds since the most recent bypass for
    this decision, or ``None`` if no bypass is in the window. A bypass
    inside ``_BYPASS_RECENCY_WINDOW_SECONDS`` (one hour) drops the
    action one ladder rung. ``ignore`` cannot drop further.
    """
    if recency is None or recency >= _BYPASS_RECENCY_WINDOW_SECONDS:
        return action
    if action not in _ACTION_LADDER:
        return action
    idx = _ACTION_LADDER.index(action)
    if idx == 0:
        return action
    return _ACTION_LADDER[idx - 1]


def _apply_max_native_ceiling(action: str, config: GovernanceConfig) -> str:
    """Cap the action at ``config.max_native_action``.

    ``allow_blocking`` is locked at ``Literal[False]`` — pydantic
    refuses any other value — so this helper has no special case for
    it. Anything stronger than the ceiling is clamped to the ceiling.
    """
    if action not in _ACTION_LADDER:
        return action
    cap = config.max_native_action
    if cap not in _ACTION_LADDER:
        return action
    return _ACTION_LADDER[min(_ACTION_LADDER.index(action), _ACTION_LADDER.index(cap))]


def _gate_name_for(metadata: GovernanceMetadata) -> str:
    """Stable label identifying which gate evaluated the finding.

    Format: ``governance:<decision_class>``. Surfaced in the audit
    trail so a reviewer can see at a glance which class policy fired.
    """
    return f"governance:{metadata.decision_class}"


def _compose_reason(
    matched: list[str],
    missing: list[str],
    finding: GovernanceFinding,
    metadata: GovernanceMetadata,
    action: str,
) -> str:
    """Human-readable reason string. Stable wording for audit grep."""
    parts = [
        f"action={action}",
        f"semantic_status={finding.semantic_status}",
        f"decision_class={metadata.decision_class}",
        f"risk_class={metadata.risk_class}",
    ]
    if matched:
        parts.append(f"matched={','.join(matched)}")
    if missing:
        parts.append(f"missing={','.join(missing)}")
    return "; ".join(parts)


def _max_action(a: str, b: str) -> str:
    """Return the higher-severity of two ladder positions."""
    if a not in _ACTION_LADDER:
        return b
    if b not in _ACTION_LADDER:
        return a
    return _ACTION_LADDER[max(_ACTION_LADDER.index(a), _ACTION_LADDER.index(b))]


def _confidence_value(raw: float | str | None) -> float:
    """Coerce a confidence dict value to a float in [0, 1].

    String labels follow the ComplianceVerdict convention:
    ``high`` = 0.9, ``medium`` = 0.6, ``low`` = 0.3. Unknown strings
    return 0.0 (treated as below any threshold).
    """
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        return {"high": 0.9, "medium": 0.6, "low": 0.3}.get(raw.lower(), 0.0)
    return 0.0
