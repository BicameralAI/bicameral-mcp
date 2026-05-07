"""Phase 4 (#61) — drift classification service.

Wires the deterministic ``drift_classifier`` into the ledger I/O
layer. Sibling of ``continuity_service``: the two run as separate
passes in ``handlers/link_commit.py`` (continuity = "where did this
go?", drift_service = "did the meaning change?").

For one drifted region:

1. Load stored ``subject_identity`` (signature_hash + neighbors).
2. Call ``classify_drift`` with old/new bodies + baselines.
3. Dispatch by verdict:
   - ``cosmetic`` (score >= 0.80) → write ``compliance_check`` with
     ``verdict="compliant", semantic_status="semantically_preserved"``
     + ``evidence_refs``; ``auto_resolved=True``.
   - ``uncertain`` (0.30 < score < 0.80) → emit
     ``PreClassificationHint`` for the caller LLM; no write.
   - ``semantic`` (score <= 0.30) → no write, no hint.

Failure-isolated: any exception → ``_NO_OUTCOME``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from contracts import PreClassificationHint

from .adapter import CodeGenomeAdapter
from .continuity_service import _load_best_identity
from .drift_classifier import DriftClassification, classify_drift

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftClassificationContext:
    """Inputs to ``evaluate_drift_classification``.

    ``language`` matches the keys of
    ``code_locator.indexing.symbol_extractor._LANG_PACKAGE_MAP``.
    ``content_hash`` + ``commit_hash`` are write-key fields, not
    classifier inputs.
    """

    decision_id: str
    region_id: str
    content_hash: str
    commit_hash: str
    file_path: str
    symbol_name: str
    old_body: str
    new_body: str
    language: str


@dataclass(frozen=True)
class DriftClassificationOutcome:
    """Result of one ``evaluate_drift_classification`` call."""

    classification: DriftClassification | None
    auto_resolved: bool
    pre_classification_hint: PreClassificationHint | None


_NO_OUTCOME = DriftClassificationOutcome(
    classification=None,
    auto_resolved=False,
    pre_classification_hint=None,
)


def _hint_from_classification(c: DriftClassification) -> PreClassificationHint:
    """Convert a classifier result into the typed hint that the caller
    LLM sees on ``PendingComplianceCheck.pre_classification``."""
    return PreClassificationHint(
        verdict=c.verdict,
        confidence=c.confidence,
        signals=dict(c.signals),
        evidence_refs=list(c.evidence_refs),
    )


async def _write_auto_resolution(
    ledger,
    ctx: DriftClassificationContext,
    classification: DriftClassification,
) -> None:
    """Persist the auto-resolved ``compliance_check`` row.

    Uses the existing ``upsert_compliance_check`` query (Phase 1's
    additive ``semantic_status`` + ``evidence_refs`` kwargs). The
    ``commit_hash`` field is left empty when not available — that's
    backward-compatible with the original ``upsert`` contract.
    """
    inner = getattr(ledger, "_client", ledger)
    from ledger.queries import upsert_compliance_check

    await upsert_compliance_check(
        inner,
        decision_id=ctx.decision_id,
        region_id=ctx.region_id,
        content_hash=ctx.content_hash,
        verdict="compliant",
        confidence="high",
        explanation="auto-classified as cosmetic change",
        phase="drift",
        commit_hash=ctx.commit_hash,
        ephemeral=False,
        semantic_status="semantically_preserved",
        evidence_refs=list(classification.evidence_refs),
    )


async def _write_or_hint(
    ledger,
    ctx: DriftClassificationContext,
    classification: DriftClassification,
) -> DriftClassificationOutcome:
    """O5 helper — encapsulate the 3-branch verdict dispatch.

    Keeps ``evaluate_drift_classification`` body to a flat 3-statement
    happy path: load identity → classify → dispatch.
    """
    if classification.verdict == "cosmetic" and classification.confidence >= 0.80:
        await _write_auto_resolution(ledger, ctx, classification)
        return DriftClassificationOutcome(
            classification=classification,
            auto_resolved=True,
            pre_classification_hint=None,
        )
    if classification.verdict == "uncertain":
        return DriftClassificationOutcome(
            classification=classification,
            auto_resolved=False,
            pre_classification_hint=_hint_from_classification(classification),
        )
    return DriftClassificationOutcome(
        classification=classification,
        auto_resolved=False,
        pre_classification_hint=None,
    )


def _get_current_neighbors(
    code_locator,
    file_path: str,
    start_line: int,
    end_line: int,
) -> Iterable[str] | None:
    """Fetch 1-hop neighbors via Phase 3's ``code_locator.neighbors_for``.
    Returns None on missing locator / missing method / exception
    (classifier downgrades neighbors signal to 0.0)."""
    if code_locator is None or not hasattr(code_locator, "neighbors_for"):
        return None
    try:
        return code_locator.neighbors_for(file_path, start_line, end_line)
    except Exception as exc:
        logger.debug("[drift_service] neighbors_for failed: %s", exc)
        return None


def _compute_new_signature_hash(
    codegenome: CodeGenomeAdapter,
    file_path: str,
    new_start_line: int,
    new_end_line: int,
    repo_ref: str,
) -> str | None:
    """Recompute signature hash for the region's current location.
    Returns ``None`` on missing line numbers, missing adapter method,
    or compute exception — classifier handles None as 0.5 signal."""
    if not (new_start_line and new_end_line):
        return None
    if not hasattr(codegenome, "compute_identity"):
        return None
    try:
        identity = codegenome.compute_identity(
            file_path=file_path,
            start_line=new_start_line,
            end_line=new_end_line,
            repo_ref=repo_ref,
        )
    except Exception as exc:
        logger.debug("[drift_service] new identity compute failed: %s", exc)
        return None
    return getattr(identity, "signature_hash", None)


async def _classify_with_loaded_identity(
    *,
    old_identity,
    codegenome,
    code_locator,
    ctx: DriftClassificationContext,
    new_start_line: int,
    new_end_line: int,
    repo_ref: str,
    new_signature_hash: str | None,
):
    """Build the classifier inputs and call ``classify_drift``.

    Returns the ``DriftClassification`` or ``None`` on classifier
    exception. Extracted out of ``evaluate_drift_classification`` to
    keep the entry function under the razor cap.
    """
    new_neighbors = _get_current_neighbors(
        code_locator,
        ctx.file_path,
        new_start_line,
        new_end_line,
    )
    if new_signature_hash is None:
        new_signature_hash = _compute_new_signature_hash(
            codegenome,
            ctx.file_path,
            new_start_line,
            new_end_line,
            repo_ref,
        )
    try:
        return classify_drift(
            ctx.old_body,
            ctx.new_body,
            old_signature_hash=old_identity.signature_hash,
            new_signature_hash=new_signature_hash,
            old_neighbors=old_identity.neighbors_at_bind,
            new_neighbors=new_neighbors,
            language=ctx.language,
        )
    except Exception as exc:
        logger.warning("[drift_service] classify_drift raised: %s", exc)
        return None


async def evaluate_drift_classification(
    *,
    ledger,
    codegenome: CodeGenomeAdapter,
    code_locator,
    ctx: DriftClassificationContext,
    new_start_line: int = 0,
    new_end_line: int = 0,
    repo_ref: str = "HEAD",
    new_signature_hash: str | None = None,
) -> DriftClassificationOutcome:
    """Phase 4 (#61) entry point. Section 4 razor compliant.

    ``new_signature_hash`` may be passed pre-computed (Phase 4 phase 4
    handler will plumb it from a fresh ``compute_identity`` call); if
    not, this function tries to recompute via the codegenome adapter.

    Failure-isolated: identity-load failure or classifier exception
    returns ``_NO_OUTCOME`` (no auto-resolve, no hint). Caller
    proceeds with the unmodified ``PendingComplianceCheck``.
    """
    try:
        old_id, old_identity = await _load_best_identity(ledger, ctx.decision_id)
    except Exception as exc:
        logger.debug("[drift_service] identity load failed: %s", exc)
        return _NO_OUTCOME
    if old_identity is None:
        return _NO_OUTCOME
    classification = await _classify_with_loaded_identity(
        old_identity=old_identity,
        codegenome=codegenome,
        code_locator=code_locator,
        ctx=ctx,
        new_start_line=new_start_line,
        new_end_line=new_end_line,
        repo_ref=repo_ref,
        new_signature_hash=new_signature_hash,
    )
    if classification is None:
        return _NO_OUTCOME
    try:
        return await _write_or_hint(ledger, ctx, classification)
    except Exception as exc:
        logger.warning(
            "[drift_service] write_or_hint raised for decision_id=%s: %s",
            ctx.decision_id,
            exc,
        )
        return _NO_OUTCOME
