"""CodeGenome adapter boundary — stable interface over experimental internals.

Bicameral handlers depend on this ABC and the dataclasses below. Concrete
implementations (deterministic, embedding-backed, etc.) plug in behind it.

Phase 1+2 (#59): only ``compute_identity`` is required to be useful. The
other three methods raise ``NotImplementedError`` until phases 3-5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EvidenceType = Literal[
    "code",
    "test",
    "diff",
    "runtime",
    "doc",
    "decision",
    "agent_eval",
    "manual",
]

DriftStatus = Literal[
    "reflected",
    "drifted",
    "pending",
    "ungrounded",
    "semantically_preserved",
    "needs_review",
]


@dataclass(frozen=True)
class SubjectCandidate:
    address: str
    file_path: str
    start_line: int
    end_line: int
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str


@dataclass(frozen=True)
class SubjectIdentity:
    address: str
    identity_type: str
    structural_signature: str | None
    behavioral_signature: str | None
    signature_hash: str | None
    content_hash: str | None
    confidence: float
    model_version: str
    # Phase 3 (#60): 1-hop call-graph neighbor addresses captured at bind
    # time, used by ContinuityMatcher to score Jaccard overlap between
    # pre-rebase and post-rebase neighbors. ``None`` for Phase 1+2 rows
    # written before this field existed; empty tuple for explicit "no
    # neighbors known"; non-empty sorted tuple otherwise.
    neighbors_at_bind: tuple[str, ...] | None = None


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_type: EvidenceType
    source_ref: str
    summary: str
    confidence: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DriftEvaluation:
    decision_id: str
    repo_ref: str
    status: DriftStatus
    confidence: float
    rationale: str
    supporting_evidence: list[EvidenceRecord]
    contradicting_evidence: list[EvidenceRecord]


@dataclass(frozen=True)
class EvidencePacket:
    packet_id: str
    query_text: str
    repo_ref: str
    subjects: list[SubjectCandidate]
    supporting_evidence: list[EvidenceRecord]
    contradicting_evidence: list[EvidenceRecord]
    uncertainties: list[str]
    confidence_summary: dict[str, float]


class CodeGenomeAdapter:
    """Stable adapter between Bicameral and CodeGenome.

    Bicameral handlers must depend on this interface, never on a concrete
    implementation's internals.
    """

    def resolve_subjects(
        self,
        claim_text: str,
        repo_ref: str = "HEAD",
        max_candidates: int = 10,
    ) -> list[SubjectCandidate]:
        raise NotImplementedError

    def compute_identity(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        repo_ref: str = "HEAD",
    ) -> SubjectIdentity:
        raise NotImplementedError

    def evaluate_drift(
        self,
        decision_id: str,
        repo_ref: str = "HEAD",
    ) -> DriftEvaluation:
        raise NotImplementedError

    def build_evidence_packet(
        self,
        query_text: str,
        repo_ref: str = "HEAD",
        max_subjects: int = 10,
    ) -> EvidencePacket:
        raise NotImplementedError
