"""Phase 1 (#109) — GovernanceMetadata model unit tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from governance.contracts import GovernanceMetadata


def test_governance_metadata_defaults() -> None:
    """Default-constructed model picks transparency_first defaults."""
    m = GovernanceMetadata()
    assert m.decision_class == "product_behavior"
    assert m.risk_class == "medium"
    assert m.escalation_class == "warn"
    assert m.owner is None
    assert m.supervisor is None
    assert m.notification_channels == []
    assert m.protected_component is False
    assert m.review_after is None


def test_governance_metadata_full_construction() -> None:
    """All eight fields populate cleanly; pydantic validates literal enums."""
    m = GovernanceMetadata(
        decision_class="security",
        risk_class="critical",
        escalation_class="notify_supervisor_allowed",
        owner="alice@example.com",
        supervisor="bob@example.com",
        notification_channels=["#security", "alice@example.com"],
        protected_component=True,
        review_after="2026-12-31",
    )
    assert m.decision_class == "security"
    assert m.risk_class == "critical"
    assert m.escalation_class == "notify_supervisor_allowed"
    assert m.owner == "alice@example.com"
    assert m.supervisor == "bob@example.com"
    assert m.notification_channels == ["#security", "alice@example.com"]
    assert m.protected_component is True
    assert m.review_after == "2026-12-31"


def test_governance_metadata_rejects_unknown_decision_class() -> None:
    with pytest.raises(ValidationError):
        GovernanceMetadata(decision_class="garbage")  # type: ignore[arg-type]


def test_governance_metadata_rejects_unknown_risk_class() -> None:
    with pytest.raises(ValidationError):
        GovernanceMetadata(risk_class="apocalyptic")  # type: ignore[arg-type]


def test_governance_metadata_rejects_unknown_escalation_class() -> None:
    with pytest.raises(ValidationError):
        GovernanceMetadata(escalation_class="nuke_orbit")  # type: ignore[arg-type]


def test_governance_metadata_serializes_to_json_round_trip() -> None:
    """Round-trip via model_dump_json + model_validate_json preserves all fields."""
    original = GovernanceMetadata(
        decision_class="data_contract",
        risk_class="high",
        escalation_class="escalate",
        owner="alice",
        notification_channels=["#data"],
        protected_component=True,
    )
    serialized = original.model_dump_json()
    restored = GovernanceMetadata.model_validate_json(serialized)
    assert restored == original
