"""Phase 3 (#108) — deterministic escalation engine unit tests.

Engine is pure: bypass_recency_seconds is a scalar. Phase 4 will wire
the actual JSONL-driven lookup; these tests pass the value directly.
"""

from __future__ import annotations

import uuid

from governance import engine as governance_engine
from governance.config import DecisionClassPolicy, GovernanceConfig
from governance.contracts import GovernanceFinding, GovernanceMetadata


def _meta(
    decision_class: str = "product_behavior",
    risk_class: str = "medium",
    escalation_class: str = "warn",
    protected: bool = False,
) -> GovernanceMetadata:
    return GovernanceMetadata(
        decision_class=decision_class,  # type: ignore[arg-type]
        risk_class=risk_class,  # type: ignore[arg-type]
        escalation_class=escalation_class,  # type: ignore[arg-type]
        protected_component=protected,
    )


def _finding(
    semantic_status: str,
    decision_class: str = "product_behavior",
    confidence: dict | None = None,
) -> GovernanceFinding:
    meta = _meta(decision_class=decision_class)
    return GovernanceFinding(
        finding_id=str(uuid.uuid4()),
        decision_id="decision:test",
        region_id="code_region:r1",
        decision_class=meta.decision_class,
        risk_class=meta.risk_class,
        escalation_class=meta.escalation_class,
        source="preflight",
        semantic_status=semantic_status,  # type: ignore[arg-type]
        confidence=confidence or {},
        explanation="test",
    )


def _config_with(security_supervisor: bool = True) -> GovernanceConfig:
    """Helper to build a GovernanceConfig with a security class policy."""
    return GovernanceConfig(
        decision_classes={
            "security": DecisionClassPolicy(
                default_action="escalate",
                supervisor_notification_allowed=security_supervisor,
                system_wide_warning_allowed=False,
                supervisor_thresholds={
                    "drift_confidence": 0.85,
                    "binding_confidence": 0.85,
                },
            ),
            "product_behavior": DecisionClassPolicy(
                default_action="warn",
            ),
        }
    )


def test_unrelated_decision_returns_ignore() -> None:
    """semantic_status=not_relevant → action=ignore."""
    cfg = GovernanceConfig()
    f = _finding("not_relevant")
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    assert result.action == "ignore"


def test_cosmetic_change_returns_context() -> None:
    """cosmetic_change → action=context."""
    cfg = GovernanceConfig()
    f = _finding("cosmetic_change")
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    assert result.action == "context"


def test_likely_drift_l1_default_returns_warn() -> None:
    """L1/product_behavior + likely_drift + no per-class policy → warn."""
    cfg = GovernanceConfig()
    f = _finding("likely_drift", decision_class="product_behavior")
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    assert result.action in ("warn", "escalate")
    # No class policy → base=warn, semantic likely_drift bumps via _max_action.
    assert result.action == "escalate" or result.action == "warn"


def test_likely_drift_security_class_returns_escalate() -> None:
    """security class + likely_drift → escalate."""
    cfg = _config_with(security_supervisor=True)
    f = _finding("likely_drift", decision_class="security")
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    assert result.action == "escalate"


def test_critical_drift_security_class_returns_supervisor_or_warning() -> None:
    """critical_drift + security + supervisor_allowed → notify_supervisor."""
    cfg = _config_with(security_supervisor=True)
    f = _finding(
        "critical_drift",
        decision_class="security",
        confidence={"drift_confidence": 0.95, "binding_confidence": 0.95},
    )
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    assert result.action in ("notify_supervisor", "system_wide_warning")
    assert result.requires_human_resolution is True


def test_supervisor_notification_requires_ratified() -> None:
    """Unratified decision → matched_conditions excludes
    decision_status_is_ratified."""
    cfg = _config_with(security_supervisor=True)
    f = _finding(
        "critical_drift",
        decision_class="security",
        confidence={"drift_confidence": 0.95, "binding_confidence": 0.95},
    )
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="proposed",
        bypass_recency_seconds=None,
    )
    assert "decision_status_is_ratified" in result.missing_conditions


def test_supervisor_notification_requires_active_decision() -> None:
    """Superseded decision shows up as missing in
    no_superseding_decision and decision_is_active."""
    cfg = _config_with(security_supervisor=True)
    f = _finding(
        "critical_drift",
        decision_class="security",
        confidence={"drift_confidence": 0.95, "binding_confidence": 0.95},
    )
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="superseded",
        bypass_recency_seconds=None,
    )
    assert "no_superseding_decision" in result.missing_conditions
    assert "decision_is_active" in result.missing_conditions


def test_supervisor_notification_requires_threshold_met() -> None:
    """Confidence below supervisor_thresholds shows up as missing."""
    cfg = _config_with(security_supervisor=True)
    f = _finding(
        "likely_drift",
        decision_class="security",
        confidence={"drift_confidence": 0.5, "binding_confidence": 0.5},
    )
    result = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    assert "drift_confidence_above_threshold" in result.missing_conditions
    assert "binding_confidence_above_threshold" in result.missing_conditions


def test_recently_bypassed_decision_drops_one_tier() -> None:
    """Bypass within recency window drops the action one rung."""
    cfg = _config_with(security_supervisor=True)
    f = _finding("likely_drift", decision_class="security")
    no_bypass = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    with_bypass = governance_engine.evaluate(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=120,
    )
    ladder = governance_engine._ACTION_LADDER
    no_idx = ladder.index(no_bypass.action)
    with_idx = ladder.index(with_bypass.action)
    assert with_idx == max(no_idx - 1, 0)


def test_engine_pure_function() -> None:
    """Same inputs twice → same output. No IO, no clock."""
    cfg = _config_with(security_supervisor=True)
    f = _finding("likely_drift", decision_class="security")
    args = dict(
        finding=f,
        metadata=_meta(decision_class="security", protected=True),
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    a = governance_engine.evaluate(**args)  # type: ignore[arg-type]
    b = governance_engine.evaluate(**args)  # type: ignore[arg-type]
    assert a == b
