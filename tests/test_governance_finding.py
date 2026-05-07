"""Phase 2 (#110) — GovernanceFinding + GovernancePolicyResult contracts."""

from __future__ import annotations

import uuid

from governance.contracts import GovernanceFinding, GovernancePolicyResult


def _new_id() -> str:
    return str(uuid.uuid4())


def test_finding_minimal_construction() -> None:
    """Required fields populate; optional fields default cleanly."""
    f = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        source="preflight",
        semantic_status="possible_drift",
        explanation="why",
    )
    assert f.region_id is None
    assert f.decision_class is None
    assert f.risk_class is None
    assert f.escalation_class is None
    assert f.confidence == {}
    assert f.evidence_refs == []
    assert f.policy_result is None


def test_finding_serialization_round_trip() -> None:
    """JSON round-trip preserves every field."""
    f = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        region_id="code_region:r1",
        decision_class="security",
        risk_class="high",
        escalation_class="escalate",
        source="resolve_compliance",
        semantic_status="confirmed_drift",
        confidence={"verdict_confidence": "high", "drift_confidence": 0.92},
        explanation="signature mismatch",
        evidence_refs=["signature:1.00", "neighbors:0.97"],
    )
    serialized = f.model_dump_json()
    restored = GovernanceFinding.model_validate_json(serialized)
    assert restored == f


def test_finding_confidence_dict_typing() -> None:
    """Confidence accepts both float and string values."""
    f = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        source="drift",
        semantic_status="likely_drift",
        explanation="x",
        confidence={"drift_confidence": 0.85, "verdict_confidence": "high"},
    )
    assert f.confidence["drift_confidence"] == 0.85
    assert f.confidence["verdict_confidence"] == "high"


def test_finding_evidence_refs_optional() -> None:
    """Empty evidence_refs is the default and preserved on round-trip."""
    f = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        source="preflight",
        semantic_status="not_relevant",
        explanation="x",
    )
    assert f.evidence_refs == []
    f2 = GovernanceFinding.model_validate_json(f.model_dump_json())
    assert f2.evidence_refs == []


def test_finding_policy_result_optional() -> None:
    """Findings without a policy_result are valid; engine attaches it later."""
    f = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        source="drift",
        semantic_status="likely_drift",
        explanation="x",
    )
    assert f.policy_result is None
    pr = GovernancePolicyResult(
        action="warn",
        gate="governance:product_behavior",
        reason="...",
    )
    f2 = f.model_copy(update={"policy_result": pr})
    assert f2.policy_result is not None
    assert f2.policy_result.action == "warn"
