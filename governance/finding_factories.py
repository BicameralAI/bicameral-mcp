"""Factories for ``GovernanceFinding`` plus ``consolidate()``.

Builders translate raw signals (compliance verdicts, drift entries,
preflight drift candidates) into uniform ``GovernanceFinding``
objects so the engine can evaluate them with one code path.

``consolidate()`` collapses findings that share a ``(decision_id,
region_id)`` pair into a single finding with the highest-severity
semantic status and the union of evidence refs. Per-region granularity
is preserved: different regions for the same decision stay separate.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Literal, cast

from governance.contracts import GovernanceFinding, GovernanceMetadata

if TYPE_CHECKING:
    from contracts import (
        BriefDecision,
        ComplianceVerdict,
        DriftEntry,
    )


# Severity ordering for consolidation. Higher index = stronger claim.
# When two findings collide on (decision_id, region_id), the one whose
# semantic_status has the higher index wins; the loser's evidence_refs
# are merged into the winner's. Order is opinionated and locked here.
_SEMANTIC_SEVERITY: tuple[str, ...] = (
    "not_relevant",
    "cosmetic_change",
    "behavior_preserving_refactor",
    "binding_uncertain",
    "supersession_candidate",
    "needs_human_review",
    "possible_drift",
    "likely_drift",
    "confirmed_drift",
    "critical_drift",
)


def _new_finding_id() -> str:
    """Fresh UUIDv4 for a finding."""
    return str(uuid.uuid4())


def from_compliance_verdict(
    verdict: ComplianceVerdict,
    metadata: GovernanceMetadata,
) -> GovernanceFinding:
    """Build a finding from a single ``ComplianceVerdict``.

    Maps the three-way verdict enum to a semantic_status:
      - compliant   → ``not_relevant``
      - drifted     → ``likely_drift``
      - not_relevant → ``not_relevant``

    The caller-LLM's confidence (``"high"``/``"medium"``/``"low"``) is
    preserved verbatim under the ``"verdict_confidence"`` key in the
    finding's confidence dict.
    """
    semantic_map: dict[str, str] = {
        "compliant": "not_relevant",
        "drifted": "likely_drift",
        "not_relevant": "not_relevant",
    }
    semantic = semantic_map[verdict.verdict]
    return GovernanceFinding(
        finding_id=_new_finding_id(),
        decision_id=verdict.decision_id,
        region_id=verdict.region_id,
        decision_class=metadata.decision_class,
        risk_class=metadata.risk_class,
        escalation_class=metadata.escalation_class,
        source="resolve_compliance",
        semantic_status=cast(
            Literal[
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
            ],
            semantic,
        ),
        confidence={"verdict_confidence": verdict.confidence},
        explanation=verdict.explanation,
        evidence_refs=list(verdict.evidence_refs or []),
    )


def from_drift_entry(
    entry: DriftEntry,
    metadata: GovernanceMetadata,
    region_id: str | None = None,
) -> GovernanceFinding:
    """Build a finding from a ``DriftEntry`` (detect_drift / scan_branch).

    A drifted entry surfaces as ``likely_drift`` unless the
    cosmetic_hint flag is set, in which case the structural analyzer
    has provably proven semantics-preserving and the status downgrades
    to ``cosmetic_change``. Anything else (reflected/pending/ungrounded)
    is treated as ``not_relevant`` for governance purposes.
    """
    status_map: dict[str, str] = {
        "drifted": "cosmetic_change" if entry.cosmetic_hint else "likely_drift",
        "reflected": "not_relevant",
        "pending": "not_relevant",
        "ungrounded": "not_relevant",
    }
    semantic = status_map.get(str(entry.status), "not_relevant")
    return GovernanceFinding(
        finding_id=_new_finding_id(),
        decision_id=entry.decision_id,
        region_id=region_id,
        decision_class=metadata.decision_class,
        risk_class=metadata.risk_class,
        escalation_class=metadata.escalation_class,
        source="drift",
        semantic_status=cast(
            Literal[
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
            ],
            semantic,
        ),
        confidence={},
        explanation=entry.drift_evidence or entry.description,
    )


def from_preflight_drift_candidate(
    candidate: BriefDecision,
    metadata: GovernanceMetadata,
    region_id: str | None = None,
) -> GovernanceFinding:
    """Build a finding from a preflight ``BriefDecision`` drift candidate.

    Preflight surfaces drift candidates per region; the caller LLM has
    not yet rendered a verdict, so the semantic_status is set
    conservatively from the decision's pipeline status:
      - drifted   → ``likely_drift``
      - pending   → ``possible_drift`` (not yet verified)
      - reflected → ``not_relevant``
      - ungrounded → ``not_relevant``
    """
    status_map: dict[str, str] = {
        "drifted": "likely_drift",
        "pending": "possible_drift",
        "reflected": "not_relevant",
        "ungrounded": "not_relevant",
    }
    semantic = status_map.get(str(candidate.status), "not_relevant")
    return GovernanceFinding(
        finding_id=_new_finding_id(),
        decision_id=candidate.decision_id,
        region_id=region_id,
        decision_class=metadata.decision_class,
        risk_class=metadata.risk_class,
        escalation_class=metadata.escalation_class,
        source="preflight",
        semantic_status=cast(
            Literal[
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
            ],
            semantic,
        ),
        confidence={},
        explanation=candidate.drift_evidence or candidate.description,
    )


def consolidate(findings: list[GovernanceFinding]) -> list[GovernanceFinding]:
    """Collapse findings sharing ``(decision_id, region_id)`` into one.

    The winner is the finding whose ``semantic_status`` has the higher
    index in ``_SEMANTIC_SEVERITY``; ties go to the existing entry. The
    winner's ``evidence_refs`` are extended (order-preserving dedup)
    with the loser's. All other fields on the loser are discarded —
    if per-source explanation matters for downstream consumers, lift it
    into the evidence_refs format before consolidating.
    """
    by_key: dict[tuple[str, str | None], GovernanceFinding] = {}
    for f in findings:
        key = (f.decision_id, f.region_id)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = f
            continue
        a_idx = _SEMANTIC_SEVERITY.index(existing.semantic_status)
        b_idx = _SEMANTIC_SEVERITY.index(f.semantic_status)
        winner = f if b_idx > a_idx else existing
        loser = existing if winner is f else f
        merged_refs = list(dict.fromkeys(list(winner.evidence_refs) + list(loser.evidence_refs)))
        by_key[key] = winner.model_copy(update={"evidence_refs": merged_refs})
    return list(by_key.values())
