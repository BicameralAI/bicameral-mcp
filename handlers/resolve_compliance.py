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
from typing import Iterable

from contracts import (
    ComplianceVerdict,
    ResolveComplianceAccepted,
    ResolveComplianceRejection,
    ResolveComplianceResponse,
)
from ledger.queries import (
    decision_exists,
    delete_binds_to_edge,
    project_decision_status,
    region_exists,
    update_decision_status,
    upsert_compliance_check,
)

logger = logging.getLogger(__name__)


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

    Three-way verdict semantics:
      "compliant"    — write compliance_check(verdict='compliant'), keep binds_to
      "drifted"      — write compliance_check(verdict='drifted'), keep binds_to
      "not_relevant" — write compliance_check(verdict='not_relevant', pruned=True),
                       DELETE the binds_to edge (retrieval mistake, not drift)

    After the full batch is written, status for each affected decision is
    re-projected holistically via project_decision_status (closes the
    last-verdict-wins caveat from v0.4.x).
    """
    if phase not in _VALID_PHASES:
        raise ValueError(
            f"Unknown phase {phase!r} — must be one of {sorted(_VALID_PHASES)}"
        )

    sync_state = getattr(ctx, "_sync_state", None)
    is_ephemeral = False
    if isinstance(sync_state, dict):
        expected_flow_id = sync_state.get("pending_flow_id")
        if expected_flow_id and flow_id != expected_flow_id:
            logger.warning(
                "[resolve_compliance] flow_id mismatch: expected %s, got %s — "
                "verdicts may be stale or from a different link_commit call",
                expected_flow_id[:8], (flow_id or "missing")[:8],
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
            rejected.append(ResolveComplianceRejection(
                decision_id=v.decision_id,
                region_id=v.region_id,
                reason="unknown_decision_id",
                detail=f"no decision row for {v.decision_id}",
            ))
            continue

        if not await region_exists(client, v.region_id):
            rejected.append(ResolveComplianceRejection(
                decision_id=v.decision_id,
                region_id=v.region_id,
                reason="unknown_region_id",
                detail=f"no code_region row for {v.region_id}",
            ))
            continue

        is_pruned = v.verdict == "not_relevant"

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
        )

        # Prune the binds_to edge when the caller says "not relevant" —
        # retrieval made a mistake; remove the binding to keep the graph clean.
        if is_pruned:
            await delete_binds_to_edge(client, v.decision_id, v.region_id)

        affected_decision_ids.add(v.decision_id)

        accepted.append(ResolveComplianceAccepted(
            decision_id=v.decision_id,
            region_id=v.region_id,
            phase=phase,
            verdict=v.verdict,
        ))

    # v0.5.0: holistic status projection after the full batch is written.
    # Replaces the per-verdict last-verdict-wins update from v0.4.x.
    for decision_id in affected_decision_ids:
        projected = await project_decision_status(client, decision_id)
        await update_decision_status(client, decision_id, projected)

    logger.info(
        "[resolve_compliance] phase=%s accepted=%d rejected=%d commit=%s",
        phase, len(accepted), len(rejected), (commit_hash or "")[:8] or "n/a",
    )

    return ResolveComplianceResponse(
        phase=phase,
        accepted=accepted,
        rejected=rejected,
    )
