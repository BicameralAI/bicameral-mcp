"""Phase 1 (#109) — derive_governance_metadata L1/L2/L3 default mapping."""

from __future__ import annotations

from governance.contracts import GovernanceMetadata, derive_governance_metadata


def test_l1_default_maps_to_product_behavior_warn() -> None:
    """L1 with no explicit metadata yields (product_behavior, medium, warn)."""
    m = derive_governance_metadata("L1", None)
    assert m.decision_class == "product_behavior"
    assert m.risk_class == "medium"
    assert m.escalation_class == "warn"


def test_l2_default_maps_to_architecture_escalate() -> None:
    """L2 with no explicit metadata yields (architecture, medium, escalate)."""
    m = derive_governance_metadata("L2", None)
    assert m.decision_class == "architecture"
    assert m.risk_class == "medium"
    assert m.escalation_class == "escalate"


def test_l3_default_maps_to_implementation_preference_context_only() -> None:
    """L3 with no explicit metadata yields (implementation_preference, low, context_only)."""
    m = derive_governance_metadata("L3", None)
    assert m.decision_class == "implementation_preference"
    assert m.risk_class == "low"
    assert m.escalation_class == "context_only"


def test_explicit_metadata_overrides_l1l2l3_default() -> None:
    """L2 decision with explicit security metadata keeps the explicit value."""
    explicit = GovernanceMetadata(
        decision_class="security",
        risk_class="critical",
        escalation_class="notify_supervisor_allowed",
    )
    m = derive_governance_metadata("L2", explicit)
    assert m is explicit
    assert m.decision_class == "security"
    assert m.risk_class == "critical"
    assert m.escalation_class == "notify_supervisor_allowed"


def test_null_decision_level_falls_back_to_product_behavior_warn() -> None:
    """Pre-classification rows with decision_level=None get L1 defaults."""
    m = derive_governance_metadata(None, None)
    assert m.decision_class == "product_behavior"
    assert m.risk_class == "medium"
    assert m.escalation_class == "warn"
