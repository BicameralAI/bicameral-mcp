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
    kind: str, canonical_name: str, decision_id: str,
    region_id: str | None, repo_ref: str,
) -> bool:
    """Run the four ledger writes atomically; return ``True`` on full success.

    Steps: upsert subject → upsert identity → has_identity edge →
    decision-about-subject edge.

    PR #73 review (CodeRabbit MAJOR codegenome/bind_service.py:80):
    these four writes were previously fire-and-forget, so a failure
    on the third or fourth write left orphaned ``code_subject`` and
    ``subject_identity`` rows in the ledger with incomplete graph
    state. This implementation adds best-effort cleanup on partial
    failure: if any later write raises, the helper attempts to delete
    the rows freshly created by earlier writes (in reverse order) and
    propagates the original exception. Combined with the underlying
    UNIQUE constraints (``code_subject(kind, canonical_name)`` and
    ``subject_identity(address)``), this gives all-or-nothing
    semantics for fresh writes; same-address re-binds are still safe
    because deletes target only the rows we know we wrote.

    Empty IDs from the upserts (a drained ledger or schema mismatch)
    abort partway and log; the caller treats that as
    identity-not-written.

    ``region_id`` is the originating ``code_region`` for this bind —
    threaded through to ``link_decision_to_subject`` so the ``about``
    edge carries per-region disambiguation (CodeRabbit MAJOR
    ledger/queries.py:1567). Pass ``None`` when no specific region is
    in scope.
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

    try:
        await ledger.relate_has_identity(
            subject_id, identity_id, confidence=identity.confidence,
        )
        await ledger.link_decision_to_subject(
            decision_id, subject_id,
            region_id=region_id, confidence=identity.confidence,
        )
    except Exception:
        # Best-effort cleanup: delete the rows we created in this call
        # so the graph isn't left half-populated. Cleanup failures are
        # logged but don't override the original exception.
        await _rollback_partial_bind(ledger, subject_id, identity_id)
        raise
    return True


async def _rollback_partial_bind(
    ledger, subject_id: str, identity_id: str,
) -> None:
    """Delete subject_identity + code_subject rows when later edges fail.

    Called from ``_persist_subject_and_identity`` when ``relate_has_identity``
    or ``link_decision_to_subject`` raises after the upserts succeed.
    Each delete is idempotent and best-effort: if the row was already
    referenced by another edge (rare but possible under concurrent
    writers), the delete is logged but not re-raised.
    """
    for table_id, label in (
        (identity_id, "subject_identity"),
        (subject_id, "code_subject"),
    ):
        try:
            client = getattr(ledger, "_client", None)
            if client is None or not table_id:
                continue
            await client.execute(f"DELETE {table_id}")
        except Exception as exc:  # noqa: BLE001 — cleanup, do not propagate
            logger.warning(
                "[codegenome] partial-bind rollback failed to delete %s %s: %s",
                label, table_id, exc,
            )


def _compute_identity_for_bind(
    codegenome, file_path, start_line, end_line, repo_ref, code_locator,
):
    """Phase 1+2 path (compute_identity) vs Phase 3 path (with neighbors)."""
    if code_locator is not None and hasattr(codegenome, "compute_identity_with_neighbors"):
        return codegenome.compute_identity_with_neighbors(
            file_path=file_path, start_line=start_line, end_line=end_line,
            code_locator=code_locator, repo_ref=repo_ref,
        )
    return codegenome.compute_identity(
        file_path=file_path, start_line=start_line, end_line=end_line,
        repo_ref=repo_ref,
    )


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
    code_locator=None,
    region_id: str | None = None,
) -> SubjectIdentity | None:
    """Compute identity for the bound region and write the v11 records.

    Returns the persisted ``SubjectIdentity`` on success, ``None`` on
    persist failure. When ``code_locator`` is provided + the adapter
    supports it, the Phase-3 neighbor-aware path runs.

    ``region_id`` (PR #73 review) is the ``code_region`` row that was
    just bound to this decision; it is recorded on the ``decision -
    about -> code_subject`` edge so the per-region continuity matcher
    can disambiguate which stored identity corresponds to a given
    drifted region. Optional for backward compatibility.
    """
    identity = _compute_identity_for_bind(
        codegenome, file_path, start_line, end_line, repo_ref, code_locator,
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
        region_id=region_id,
        repo_ref=repo_ref,
    )
    return identity if persisted else None
