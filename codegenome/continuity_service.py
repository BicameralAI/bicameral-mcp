"""Continuity orchestration service for CodeGenome Phase 3.

Per-drifted-region resolution flow. Loads stored identities, runs the
continuity matcher, and on confidence >= 0.75 executes the full 7-step
auto-resolve sequence enumerated in plan-codegenome-phase-3.md (each
step's prerequisite is the previous step's return value):

    1. compute_identity_with_neighbors  → new_identity
    2. upsert_code_region               → new_region_id
    3. upsert_subject_identity          → new_identity_id
    4. write_subject_version            → new_version_id
    5. relate_has_version               → wires V1 closure
    6. write_identity_supersedes        → identity transition
    7. update_binds_to_region           → flips active binding

Returns a ``ContinuityResolution`` describing the outcome (or ``None`` if
confidence < 0.50, signaling the caller to fall through to the existing
PendingComplianceCheck flow).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from contracts import CodeRegionSummary, ContinuityResolution

from .adapter import CodeGenomeAdapter, SubjectIdentity
from .continuity import find_continuity_match

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftContext:
    """Bundle of args describing one drifted region. Reduces parameter
    count on ``evaluate_continuity_for_drift`` so the function fits the
    Section 4 razor (40-line limit)."""

    decision_id: str
    region_id: str
    old_file_path: str
    old_symbol_name: str
    old_symbol_kind: str
    old_start_line: int
    old_end_line: int
    repo_ref: str
    repo_path: str


def _summary(file_path: str, symbol: str, start: int, end: int) -> CodeRegionSummary:
    return CodeRegionSummary(file_path=file_path, symbol=symbol, lines=(start, end))


def _identity_from_dict(d: dict) -> SubjectIdentity:
    """Reconstitute a SubjectIdentity dataclass from a ledger query row."""
    nbrs = d.get("neighbors_at_bind")
    return SubjectIdentity(
        address=str(d.get("address", "")),
        identity_type=str(d.get("identity_type", "")),
        structural_signature=d.get("structural_signature"),
        behavioral_signature=d.get("behavioral_signature"),
        signature_hash=d.get("signature_hash"),
        content_hash=d.get("content_hash"),
        confidence=float(d.get("confidence") or 0.0),
        model_version=str(d.get("model_version", "")),
        neighbors_at_bind=tuple(nbrs) if nbrs is not None else None,
    )


async def _persist_resolved_match(
    *,
    ledger,
    codegenome,
    code_locator,
    decision_id: str,
    region_id: str,
    old_identity_id: str,
    code_subject_id: str,
    repo_ref: str,
    repo_path: str,
    match,
) -> str:
    """Execute steps 1–7 of the auto-resolve sequence; return new_region_id."""
    new_identity = codegenome.compute_identity_with_neighbors(
        match.new_file_path,
        match.new_start_line,
        match.new_end_line,
        code_locator=code_locator,
        repo_ref=repo_ref,
    )
    new_region_id = await ledger.upsert_code_region(
        file_path=match.new_file_path,
        symbol_name=match.new_symbol_name,
        start_line=match.new_start_line,
        end_line=match.new_end_line,
        repo=repo_path,
        content_hash=new_identity.content_hash or "",
    )
    new_identity_id = await ledger.upsert_subject_identity(new_identity)
    new_version_id = await ledger.write_subject_version(
        code_subject_id,
        repo_ref,
        match.new_file_path,
        match.new_start_line,
        match.new_end_line,
        symbol_name=match.new_symbol_name,
        symbol_kind=match.new_symbol_kind,
        content_hash=new_identity.content_hash,
        signature_hash=new_identity.signature_hash,
    )
    await ledger.relate_has_version(code_subject_id, new_version_id)
    await ledger.write_identity_supersedes(
        old_identity_id,
        new_identity_id,
        match.change_type,
        match.confidence,
    )
    await ledger.update_binds_to_region(decision_id, region_id, new_region_id)
    return new_region_id


def _build_needs_review(
    *,
    decision_id: str,
    region_id: str,
    old_loc,
    match,
) -> ContinuityResolution:
    return ContinuityResolution(
        decision_id=decision_id,
        old_code_region_id=region_id,
        new_code_region_id=None,
        semantic_status="needs_review",
        confidence=match.confidence,
        old_location=old_loc,
        new_location=None,
        rationale=f"ambiguous continuity candidate @ {match.confidence:.2f}; awaiting caller decision",
    )


def _build_resolved(
    *,
    decision_id: str,
    region_id: str,
    new_region_id: str,
    old_loc,
    match,
) -> ContinuityResolution:
    semantic = "identity_renamed" if match.change_type == "renamed" else "identity_moved"
    return ContinuityResolution(
        decision_id=decision_id,
        old_code_region_id=region_id,
        new_code_region_id=new_region_id,
        semantic_status=semantic,
        confidence=match.confidence,
        old_location=old_loc,
        new_location=_summary(
            match.new_file_path,
            match.new_symbol_name,
            match.new_start_line,
            match.new_end_line,
        ),
        rationale=f"continuity match @ {match.confidence:.2f}, change_type={match.change_type}",
    )


async def _load_best_identity(ledger, decision_id: str):
    """Pick highest-confidence stored identity. Returns ``(id, identity)`` or ``(None, None)``."""
    identities = await ledger.find_subject_identities_for_decision(decision_id)
    if not identities:
        return None, None
    old = max(identities, key=lambda d: float(d.get("confidence") or 0.0))
    return old["identity_id"], _identity_from_dict(old)


async def evaluate_continuity_for_drift(
    *,
    ledger,
    codegenome: CodeGenomeAdapter,
    code_locator,
    drift: DriftContext,
    threshold_high: float = 0.75,
    threshold_review: float = 0.50,
) -> ContinuityResolution | None:
    """Resolve continuity for one drifted region. See module docstring."""
    old_identity_id, old_identity = await _load_best_identity(ledger, drift.decision_id)
    if old_identity is None:
        return None
    match = find_continuity_match(
        old_identity,
        code_locator,
        old_symbol_name=drift.old_symbol_name,
        old_symbol_kind=drift.old_symbol_kind,
        threshold=threshold_review,
    )
    if match is None:
        return None
    old_loc = _summary(
        drift.old_file_path, drift.old_symbol_name, drift.old_start_line, drift.old_end_line
    )
    if match.confidence < threshold_high:
        return _build_needs_review(
            decision_id=drift.decision_id,
            region_id=drift.region_id,
            old_loc=old_loc,
            match=match,
        )
    code_subject_id = await _resolve_code_subject_id(ledger, drift.decision_id)
    if not code_subject_id:
        logger.warning("[continuity] no code_subject for decision_id=%s", drift.decision_id)
        return None
    new_region_id = await _persist_resolved_match(
        ledger=ledger,
        codegenome=codegenome,
        code_locator=code_locator,
        decision_id=drift.decision_id,
        region_id=drift.region_id,
        old_identity_id=old_identity_id,
        code_subject_id=code_subject_id,
        repo_ref=drift.repo_ref,
        repo_path=drift.repo_path,
        match=match,
    )
    return _build_resolved(
        decision_id=drift.decision_id,
        region_id=drift.region_id,
        new_region_id=new_region_id,
        old_loc=old_loc,
        match=match,
    )


async def _resolve_code_subject_id(ledger, decision_id: str) -> str | None:
    """Walk decision -> about -> code_subject; return the first subject id."""
    rows = await ledger._client.query(  # type: ignore[attr-defined]
        f"SELECT type::string(id) AS subject_id FROM {decision_id}->about->code_subject LIMIT 1",
    )
    if not rows:
        return None
    return str(rows[0].get("subject_id") or "") or None
