"""Handler for /bicameral.remove_source MCP tool — #278 Phase 2.

Hard-delete an input_span row + cascade-soft-delete every decision derived
from it via the ``yields`` graph edge. Audit-logged with the full
pre-deletion span content in the source_removed.completed event payload so
the action is recoverable from the event log.

Safety design (mirrors handlers/reset.py:42-91):
  - ``confirm=False`` (default) returns a dry-run plan listing the full
    input_span content + the cascaded decision ids. NO mutation.
  - ``confirm=True`` performs the cascade: soft-delete each derived
    decision (signoff.state="removed" + removed_by_source=<span_id> +
    reason), then hard-delete the input_span row and its outgoing yields
    edges. Emits ONE source_removed.completed event covering the entire
    cascade.

Idempotent:
  - Missing span_id at confirm=False → returns RemoveSourcePlan with
    span_existed=False, empty decision_ids, confirm_required=True.
  - Missing span_id at confirm=True → returns RemoveSourceResponse with
    span_existed=False, empty cascaded_decision_ids, event_logged=False.

Audit obligation:
  - ``reason`` is required (empty → ValueError).
  - Per Phase 2 Discipline #3, the source_removed.completed event payload
    contains the FULL input_span content so the action is recoverable from
    the append-only event log.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from contracts import RemoveSourcePlan, RemoveSourceResponse
from ledger.queries import (
    get_decisions_for_span,
    get_input_span_row,
    input_span_exists,
    project_decision_status,
    update_decision_status,
)

logger = logging.getLogger(__name__)


async def handle_remove_source(
    ctx,
    span_id: str,
    signer: str,
    reason: str,
    *,
    confirm: bool = False,
) -> RemoveSourcePlan | RemoveSourceResponse:
    """Cascading remove of an input_span + every decision derived from it.

    ``confirm=False`` (default) is a dry-run that returns the plan without
    touching state. The operator inspects the plan and re-invokes with
    ``confirm=True`` to perform the mutation.
    """
    if not reason or not reason.strip():
        raise ValueError("remove_source requires a non-empty 'reason' (audit-trail obligation)")

    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    span_existed = await input_span_exists(client, span_id)
    span_content: dict = {}
    decision_ids: list[str] = []
    if span_existed:
        span_content = await get_input_span_row(client, span_id) or {}
        decision_ids = await get_decisions_for_span(client, span_id)

    if not confirm:
        return RemoveSourcePlan(
            span_id=span_id,
            span_existed=span_existed,
            input_span_content=span_content,
            decision_ids=decision_ids,
        )

    # confirm=True path
    if not span_existed:
        # Idempotent: nothing to remove
        return RemoveSourceResponse(
            span_id=span_id,
            span_existed=False,
            cascaded_decision_ids=[],
            event_logged=False,
        )

    cascaded = await _apply_cascading_remove(
        client,
        span_id=span_id,
        decision_ids=decision_ids,
        signer=signer,
        session_id=getattr(ctx, "session_id", None) or "",
        head_ref=getattr(ctx, "authoritative_sha", "") or "",
        reason=reason,
    )

    # Emit one source_removed.completed event covering the entire cascade.
    # Payload carries full pre-deletion span content per Discipline #3.
    writer = getattr(ledger, "_writer", None)
    event_logged = False
    if writer is not None:
        writer.write(
            "source_removed.completed",
            {
                "span_id": span_id,
                "input_span_content": span_content,
                "cascaded_decision_ids": cascaded,
                "signer": signer,
                "reason": reason,
                "removed_at": datetime.now(UTC).isoformat(),
            },
        )
        event_logged = True

    logger.info(
        "[remove_source] span=%s signer=%s cascaded=%d event_logged=%s",
        span_id,
        signer,
        len(cascaded),
        event_logged,
    )

    return RemoveSourceResponse(
        span_id=span_id,
        span_existed=True,
        cascaded_decision_ids=cascaded,
        event_logged=event_logged,
    )


async def _apply_cascading_remove(
    client,
    *,
    span_id: str,
    decision_ids: list[str],
    signer: str,
    session_id: str,
    head_ref: str,
    reason: str,
) -> list[str]:
    """Soft-delete each decision in ``decision_ids`` and hard-delete the span
    row + its outgoing ``yields`` edges. Returns the list of decision ids
    that were actually mutated."""
    now_iso = datetime.now(UTC).isoformat()
    cascaded: list[str] = []

    for did in decision_ids:
        existing = await client.query(
            f"SELECT signoff FROM {did} LIMIT 1",
        )
        prev = existing[0].get("signoff") if existing and isinstance(existing[0], dict) else None
        previous_state = prev.get("state") if isinstance(prev, dict) else None
        # Idempotent per-decision: already-removed decisions are not re-written.
        if previous_state == "removed":
            cascaded.append(did)
            continue

        signoff = {
            "state": "removed",
            "signer": signer,
            "session_id": session_id,
            "removed_at": now_iso,
            "previous_state": previous_state,
            "reason": reason,
            "removed_by_source": span_id,
            "source_commit_ref": head_ref,
        }
        await client.query(
            f"UPDATE {did} SET signoff = $signoff",
            {"signoff": signoff},
        )
        projected = await project_decision_status(client, did)
        await update_decision_status(client, did, projected)
        cascaded.append(did)

    # Hard-delete outgoing yields edges from this span, then the span itself.
    await client.query(f"DELETE yields WHERE in = {span_id}")
    await client.query(f"DELETE {span_id}")

    return cascaded
