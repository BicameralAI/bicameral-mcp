"""Phase 2 (#110) — finding factory + consolidate() unit tests."""

from __future__ import annotations

import uuid

from contracts import (
    BriefDecision,
    ComplianceVerdict,
    DriftEntry,
)
from governance.contracts import GovernanceFinding, GovernanceMetadata
from governance.finding_factories import (
    consolidate,
    from_compliance_verdict,
    from_drift_entry,
    from_preflight_drift_candidate,
)


def _meta() -> GovernanceMetadata:
    return GovernanceMetadata(
        decision_class="architecture",
        risk_class="medium",
        escalation_class="escalate",
    )


def _new_id() -> str:
    return str(uuid.uuid4())


def test_from_compliance_verdict_extracts_decision_id_region_hash() -> None:
    """from_compliance_verdict pulls decision_id, region_id, and confidence."""
    verdict = ComplianceVerdict(
        decision_id="decision:abc",
        region_id="code_region:r1",
        content_hash="hash123",
        verdict="drifted",
        confidence="high",
        explanation="signature mismatch",
        evidence_refs=["signature:1.00"],
    )
    f = from_compliance_verdict(verdict, _meta())
    assert f.decision_id == "decision:abc"
    assert f.region_id == "code_region:r1"
    assert f.source == "resolve_compliance"
    assert f.semantic_status == "likely_drift"
    assert f.confidence.get("verdict_confidence") == "high"
    assert "signature:1.00" in f.evidence_refs


def test_from_drift_entry_extracts_decision_id_region() -> None:
    """from_drift_entry pulls decision_id and maps drifted → likely_drift."""
    entry = DriftEntry(
        decision_id="decision:xyz",
        description="d",
        status="drifted",
        symbol="foo",
        lines=(1, 10),
        drift_evidence="evidence",
        source_ref="ref",
    )
    f = from_drift_entry(entry, _meta(), region_id="code_region:r2")
    assert f.decision_id == "decision:xyz"
    assert f.region_id == "code_region:r2"
    assert f.source == "drift"
    assert f.semantic_status == "likely_drift"
    assert f.explanation == "evidence"


def test_from_preflight_drift_candidate_extracts_status_to_semantic() -> None:
    """from_preflight_drift_candidate maps pipeline status → semantic_status."""
    drifted = BriefDecision(
        decision_id="decision:p1",
        description="d",
        status="drifted",
        drift_evidence="ev",
    )
    f = from_preflight_drift_candidate(drifted, _meta())
    assert f.semantic_status == "likely_drift"
    assert f.source == "preflight"

    pending = BriefDecision(
        decision_id="decision:p2",
        description="d",
        status="pending",
    )
    f2 = from_preflight_drift_candidate(pending, _meta())
    assert f2.semantic_status == "possible_drift"

    reflected = BriefDecision(
        decision_id="decision:p3",
        description="d",
        status="reflected",
    )
    f3 = from_preflight_drift_candidate(reflected, _meta())
    assert f3.semantic_status == "not_relevant"


def test_consolidate_dedupes_findings_per_decision_region_pair() -> None:
    """Two findings on same (decision_id, region_id) collapse into one
    with merged evidence_refs."""
    base_kwargs = dict(
        decision_id="decision:abc",
        region_id="code_region:r1",
        source="preflight",
        explanation="x",
    )
    f_low = GovernanceFinding(
        finding_id=_new_id(),
        semantic_status="cosmetic_change",
        evidence_refs=["a", "b"],
        **base_kwargs,  # type: ignore[arg-type]
    )
    f_high = GovernanceFinding(
        finding_id=_new_id(),
        semantic_status="likely_drift",
        evidence_refs=["b", "c"],
        **base_kwargs,  # type: ignore[arg-type]
    )
    merged = consolidate([f_low, f_high])
    assert len(merged) == 1
    winner = merged[0]
    assert winner.semantic_status == "likely_drift"
    # Order-preserving dedup: winner's refs first, loser's appended.
    assert winner.evidence_refs == ["b", "c", "a"]


def test_consolidate_picks_highest_severity_semantic_status() -> None:
    """Severity ladder picks confirmed_drift over likely_drift over
    possible_drift over cosmetic_change over not_relevant."""
    base = dict(
        decision_id="decision:abc",
        region_id="code_region:r1",
        source="drift",
        explanation="x",
    )
    findings = [
        GovernanceFinding(
            finding_id=_new_id(),
            semantic_status="not_relevant",
            **base,  # type: ignore[arg-type]
        ),
        GovernanceFinding(
            finding_id=_new_id(),
            semantic_status="cosmetic_change",
            **base,  # type: ignore[arg-type]
        ),
        GovernanceFinding(
            finding_id=_new_id(),
            semantic_status="possible_drift",
            **base,  # type: ignore[arg-type]
        ),
        GovernanceFinding(
            finding_id=_new_id(),
            semantic_status="likely_drift",
            **base,  # type: ignore[arg-type]
        ),
        GovernanceFinding(
            finding_id=_new_id(),
            semantic_status="confirmed_drift",
            **base,  # type: ignore[arg-type]
        ),
    ]
    merged = consolidate(findings)
    assert len(merged) == 1
    assert merged[0].semantic_status == "confirmed_drift"


def test_consolidate_keeps_separate_when_region_differs() -> None:
    """Different regions for the same decision stay as separate findings."""
    f1 = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        region_id="code_region:r1",
        source="preflight",
        semantic_status="likely_drift",
        explanation="x",
    )
    f2 = GovernanceFinding(
        finding_id=_new_id(),
        decision_id="decision:abc",
        region_id="code_region:r2",
        source="preflight",
        semantic_status="likely_drift",
        explanation="x",
    )
    merged = consolidate([f1, f2])
    assert len(merged) == 2
    region_ids = {m.region_id for m in merged}
    assert region_ids == {"code_region:r1", "code_region:r2"}
