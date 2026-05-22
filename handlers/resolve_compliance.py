"""Handler for /bicameral.resolve_compliance MCP tool — v0.5.0.

v0.5.0 changes from v0.4.x:
  - verdict field replaces compliant:bool with three-way enum
    ("compliant" | "drifted" | "not_relevant")
  - "not_relevant" prunes the binds_to edge (retrieval mistake) and writes
    compliance_check with pruned=true for audit trail
  - decision_id replaces intent_id (clean break, no aliases)
  - status is projected holistically via project_decision_status after all
    verdicts in the batch are written (closes last-verdict-wins caveat)

SAMPLING MIGRATION NOTE
-----------------------
This tool exists because MCP sampling (server-initiated LLM sub-call) is
not yet supported by Claude Code for third-party servers. Once sampling
lands, the intended flow is for link_commit to fire sampling/createMessage
with the pending checks, receive verdicts inline, and write them itself —
making this tool an internal helper rather than a public MCP tool.

flow_id ties this call back to the link_commit that generated the checks.
A missing or mismatched flow_id logs a warning (stale/orphaned call). This
will become a hard error once the codebase fully migrates to flow_id usage.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from contracts import (
    ComplianceVerdict,
    ResolveComplianceAccepted,
    ResolveComplianceRejection,
    ResolveComplianceResponse,
)
from ledger.queries import (
    compliance_history_summary,
    decision_exists,
    delete_binds_to_edge,
    get_canonical_id,
    get_decision_source,
    get_region_descriptor,
    has_prior_compliant_verdict,
    project_decision_status,
    promote_ephemeral_verdict,
    region_exists,
    update_decision_status,
    update_region_hash,
    upsert_compliance_check,
)

logger = logging.getLogger(__name__)


def _emit_m2_ratification(
    *,
    decision_id: str,
    decision_source: str | None,
    verdict: str,
    confidence: str | None,
) -> None:
    """Fire-and-forget M2 ratification event (#280 PR-3).

    Wraps ``m2_grounding_log.record_ratification`` in try/except so a
    telemetry failure never breaks ratification.
    """
    try:
        from m2_grounding_log import record_ratification

        record_ratification(
            decision_id=decision_id,
            decision_source=decision_source,
            verdict=verdict,
            confidence=confidence,
        )
    except Exception as exc:
        logger.debug("[resolve_compliance] m2 telemetry emit failed (non-fatal): %s", exc)


_VALID_PHASES = {"ingest", "drift", "regrounding", "supersession", "divergence"}


def _coerce_verdicts(raw: Iterable[dict | ComplianceVerdict]) -> list[ComplianceVerdict]:
    """Accept dicts (from MCP JSON) or already-validated models."""
    out: list[ComplianceVerdict] = []
    for item in raw:
        if isinstance(item, ComplianceVerdict):
            out.append(item)
        else:
            out.append(ComplianceVerdict.model_validate(item))
    return out


async def handle_resolve_compliance(
    ctx,
    phase: str,
    verdicts: Iterable[dict | ComplianceVerdict],
    commit_hash: str | None = None,
    flow_id: str | None = None,
) -> ResolveComplianceResponse:
    """Persist a batch of caller-LLM compliance verdicts.

    Four-way verdict semantics (#405 added ``partial``):
      "compliant"    — write compliance_check(verdict='compliant'), keep binds_to
      "drifted"      — write compliance_check(verdict='drifted'), keep binds_to.
                       REQUIRES a prior 'compliant' verdict for the same
                       (decision_id, region_id) pair (reflected-before-drifted
                       invariant). Otherwise the verdict is rejected with
                       reason='state_transition_invalid' and the caller-LLM
                       should downgrade to 'partial' and retry.
      "partial"      — write compliance_check(verdict='partial'), keep binds_to.
                       The correct verdict for binding-as-anchor-for-future-work
                       and for any never-compliant region the caller would
                       otherwise have called 'drifted'.
      "not_relevant" — write compliance_check(verdict='not_relevant', pruned=True),
                       DELETE the binds_to edge (retrieval mistake, not drift)

    After the full batch is written, status for each affected decision is
    re-projected holistically via project_decision_status (closes the
    last-verdict-wins caveat from v0.4.x).
    """
    if phase not in _VALID_PHASES:
        raise ValueError(f"Unknown phase {phase!r} — must be one of {sorted(_VALID_PHASES)}")

    sync_state = getattr(ctx, "_sync_state", None)
    is_ephemeral = False
    if isinstance(sync_state, dict):
        expected_flow_id = sync_state.get("pending_flow_id")
        if expected_flow_id and flow_id != expected_flow_id:
            logger.warning(
                "[resolve_compliance] flow_id mismatch: expected %s, got %s — "
                "verdicts may be stale or from a different link_commit call",
                expected_flow_id[:8],
                (flow_id or "missing")[:8],
            )
        elif expected_flow_id and not flow_id:
            logger.warning(
                "[resolve_compliance] called without flow_id — pass the flow_id "
                "from the preceding link_commit response to tie these calls together"
            )
        if expected_flow_id and flow_id == expected_flow_id:
            is_ephemeral = sync_state.get("pending_ephemeral", False)

    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    parsed = _coerce_verdicts(verdicts)

    accepted: list[ResolveComplianceAccepted] = []
    rejected: list[ResolveComplianceRejection] = []
    affected_decision_ids: set[str] = set()

    for v in parsed:
        if not await decision_exists(client, v.decision_id):
            rejected.append(
                ResolveComplianceRejection(
                    decision_id=v.decision_id,
                    region_id=v.region_id,
                    reason="unknown_decision_id",
                    detail=f"no decision row for {v.decision_id}",
                )
            )
            continue

        if not await region_exists(client, v.region_id):
            rejected.append(
                ResolveComplianceRejection(
                    decision_id=v.decision_id,
                    region_id=v.region_id,
                    reason="unknown_region_id",
                    detail=f"no code_region row for {v.region_id}",
                )
            )
            continue

        # #405 — reflected-before-drifted invariant. You cannot drift from a
        # state you never reached. If the caller submits 'drifted' for a
        # (decision, region) pair with no prior 'compliant' row, return a
        # structured rejection that tells the caller to downgrade to 'partial'
        # and retry. No row is written for this verdict.
        if v.verdict == "drifted" and not await has_prior_compliant_verdict(
            client, v.decision_id, v.region_id
        ):
            history = await compliance_history_summary(client, v.decision_id, v.region_id)
            rejected.append(
                ResolveComplianceRejection(
                    decision_id=v.decision_id,
                    region_id=v.region_id,
                    reason="state_transition_invalid",
                    detail=(
                        "cannot transition to 'drifted' — no prior 'compliant' verdict "
                        "exists for this (decision_id, region_id) pair. Downgrade to "
                        "'partial' (never-compliant anticipatory binding) and retry."
                    ),
                    attempted_verdict="drifted",
                    allowed_verdicts=["compliant", "partial", "not_relevant"],
                    prior_history_summary=history,
                )
            )
            continue

        is_pruned = v.verdict == "not_relevant"

        # V2: promote ephemeral=True → False when this hash is confirmed non-ephemeral.
        # The UNIQUE index on (d,r,h) means upsert_compliance_check is a no-op if the
        # row already exists with ephemeral=True, so we must UPDATE it first.
        if not is_ephemeral and v.content_hash:
            try:
                await promote_ephemeral_verdict(client, v.decision_id, v.region_id, v.content_hash)
            except Exception as exc:
                logger.warning(
                    "[resolve_compliance] promote_ephemeral_verdict failed for %s: %s",
                    v.decision_id,
                    exc,
                )

        await upsert_compliance_check(
            client,
            decision_id=v.decision_id,
            region_id=v.region_id,
            content_hash=v.content_hash,
            verdict=v.verdict,
            confidence=v.confidence,
            explanation=v.explanation,
            phase=phase,
            commit_hash=commit_hash or "",
            pruned=is_pruned,
            ephemeral=is_ephemeral,
            # Phase 4 (#61): caller's optional semantic claim +
            # supporting evidence. Both default to None / [] when the
            # caller doesn't supply them — fully backward-compatible.
            semantic_status=getattr(v, "semantic_status", None),
            evidence_refs=list(getattr(v, "evidence_refs", []) or []),
        )

        # #190: emit compliance_check.completed to the team-sync JSONL stream
        # so peer replays can re-apply the verdict without re-evaluating
        # locally. No-op in single-mode (no apply_resolve_compliance method).
        emit = getattr(ledger, "apply_resolve_compliance", None)
        if emit is not None:
            canonical_id = await get_canonical_id(client, v.decision_id)
            descriptor = await get_region_descriptor(client, v.region_id)
            if canonical_id and descriptor:
                await emit(
                    canonical_decision_id=canonical_id,
                    repo=descriptor["repo"],
                    file_path=descriptor["file_path"],
                    symbol_name=descriptor["symbol_name"],
                    content_hash=descriptor["content_hash"],
                    verdict=v.verdict,
                    pinned_commit=ctx.head_sha,
                    evidence=v.explanation,
                )

        # Prune the binds_to edge when the caller says "not relevant" —
        # retrieval made a mistake; remove the binding to keep the graph clean.
        if is_pruned:
            await delete_binds_to_edge(client, v.decision_id, v.region_id)

        affected_decision_ids.add(v.decision_id)

        accepted.append(
            ResolveComplianceAccepted(
                decision_id=v.decision_id,
                region_id=v.region_id,
                phase=phase,
                verdict=v.verdict,
                semantic_status=getattr(v, "semantic_status", None),
            )
        )

        # #280 PR-3 — M2 grounding-precision ratification telemetry.
        # Best-effort source lookup (single-field query). On failure, fall
        # back to "unknown" rather than blocking the verdict write.
        try:
            decision_source = await get_decision_source(client, v.decision_id)
        except Exception:
            decision_source = None
        _emit_m2_ratification(
            decision_id=v.decision_id,
            decision_source=decision_source,
            verdict=v.verdict,
            confidence=v.confidence,
        )

    # Sync code_region.content_hash to the verdict hash for every accepted verdict.
    # project_decision_status looks up verdicts by (decision_id, region_id,
    # code_region.content_hash). When link_commit ran on a non-authoritative branch
    # it skipped update_region_hash, leaving code_region.content_hash stale (often
    # ""). Without this sync the verdict lookup returns None → status stays pending.
    for v in parsed:
        if not v.region_id or not v.content_hash:
            continue
        try:
            await update_region_hash(client, v.region_id, v.content_hash)
        except Exception as exc:
            logger.warning(
                "[resolve_compliance] update_region_hash failed for %s: %s", v.region_id, exc
            )

    # v0.5.0: holistic status projection after the full batch is written.
    # Replaces the per-verdict last-verdict-wins update from v0.4.x.
    for decision_id in affected_decision_ids:
        projected = await project_decision_status(client, decision_id)
        await update_decision_status(client, decision_id, projected)

    logger.info(
        "[resolve_compliance] phase=%s accepted=%d rejected=%d commit=%s",
        phase,
        len(accepted),
        len(rejected),
        (commit_hash or "")[:8] or "n/a",
    )

    return ResolveComplianceResponse(
        phase=phase,
        accepted=accepted,
        rejected=rejected,
    )
