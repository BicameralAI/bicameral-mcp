"""Bind-time CodeGenome identity write path.

Side-effect-only service called by ``handlers/bind.py`` after
``ledger.bind_decision()`` succeeds, when ``codegenome.identity_writes_active()``
is True. Off by default; v11 schema defines the tables unconditionally so
toggling the flag does not require a migration.

The bind handler's response contract is unchanged. If this service raises,
the handler logs and continues — identity records are best-effort
enrichment, not part of the contract.
"""

from __future__ import annotations

import logging

from .adapter import CodeGenomeAdapter, SubjectIdentity

logger = logging.getLogger(__name__)


def _check_hash_parity(
    identity: SubjectIdentity,
    code_region_content_hash: str,
    decision_id: str,
    file_path: str,
    start_line: int,
    end_line: int,
) -> None:
    """Warn if the identity hash and code_region hash diverge.

    Both code paths use ``ledger.status.hash_lines``, so equality is
    guaranteed by construction in the deterministic_location_v1 path.
    A divergence indicates a bug; do not abort, just log.
    """
    if not code_region_content_hash or not identity.content_hash:
        return
    if code_region_content_hash == identity.content_hash:
        return
    logger.warning(
        "[codegenome] identity content_hash %s != region content_hash %s "
        "(decision_id=%s, %s:%d-%d) — writing identity anyway",
        identity.content_hash, code_region_content_hash,
        decision_id, file_path, start_line, end_line,
    )


async def _persist_subject_and_identity(
    *, ledger, identity: SubjectIdentity,
    kind: str, canonical_name: str, decision_id: str, repo_ref: str,
) -> bool:
    """Run the four ledger writes; return ``True`` on full success.

    Steps: upsert subject → upsert identity → has_identity edge →
    decision-about-subject edge. Empty IDs from the upserts (a drained
    ledger or schema mismatch) abort partway and log; the caller treats
    that as identity-not-written.
    """
    subject_id = await ledger.upsert_code_subject(
        kind=kind, canonical_name=canonical_name,
        current_confidence=identity.confidence, repo_ref=repo_ref,
    )
    if not subject_id:
        logger.warning(
            "[codegenome] upsert_code_subject empty id for %s/%s",
            kind, canonical_name,
        )
        return False

    identity_id = await ledger.upsert_subject_identity(identity)
    if not identity_id:
        logger.warning(
            "[codegenome] upsert_subject_identity empty id for %s",
            identity.address,
        )
        return False

    await ledger.relate_has_identity(subject_id, identity_id, confidence=identity.confidence)
    await ledger.link_decision_to_subject(decision_id, subject_id, confidence=identity.confidence)
    return True


async def write_codegenome_identity(
    *,
    ledger,
    codegenome: CodeGenomeAdapter,
    decision_id: str,
    file_path: str,
    symbol_name: str,
    symbol_kind: str,
    start_line: int,
    end_line: int,
    repo_ref: str = "HEAD",
    code_region_content_hash: str = "",
) -> SubjectIdentity | None:
    """Compute identity for the bound region and write the v11 records.

    Returns the persisted ``SubjectIdentity`` on success, or ``None`` if
    the underlying ledger writes did not complete (empty IDs from the
    upserts). The identity is computed regardless; the return shape
    distinguishes "computed and persisted" from "computed only".
    """
    identity = codegenome.compute_identity(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        repo_ref=repo_ref,
    )
    _check_hash_parity(
        identity, code_region_content_hash,
        decision_id, file_path, start_line, end_line,
    )
    persisted = await _persist_subject_and_identity(
        ledger=ledger,
        identity=identity,
        kind=symbol_kind or "unknown",
        canonical_name=symbol_name or file_path,
        decision_id=decision_id,
        repo_ref=repo_ref,
    )
    return identity if persisted else None
