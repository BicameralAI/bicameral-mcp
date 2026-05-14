"""Handler for /bicameral.remove_decision MCP tool — #278 Phase 2.

Soft-delete a decision: the row stays, signoff.state flips to "removed",
and a decision_removed.completed event is appended to the event log when
team mode is active.

Like rejection, removed decisions remain visible as negative signals — agents
consult them to avoid re-introducing the same wrong decision. Restoration
requires writing a new decision that supersedes the removed one (mirror of
the ratify.py "No unratify" doctrine).

Audit obligation:
  - `reason` is required (empty string raises ValueError).
  - Every state mutation appends decision_removed.completed to the event log
    when ledger has an attached event writer (team mode); local-only mode
    skips the event emission (no writer attached).
  - The signoff dict carries `previous_state` so the event log payload
    captures the full state transition.

Idempotent:
  - Calling remove_decision on an already-removed decision returns
    was_new=False, does not write a new signoff, and does not emit a
    second event.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from contracts import RemoveDecisionResponse
from ledger.queries import (
    decision_exists,
    project_decision_status,
    update_decision_status,
)

logger = logging.getLogger(__name__)


async def handle_remove_decision(
    ctx,
    decision_id: str,
    signer: str,
    reason: str,
) -> RemoveDecisionResponse:
    """Soft-delete a decision (signoff.state -> "removed").

    Idempotent: a second call returns was_new=False and leaves the existing
    "removed" signoff untouched.
    """
    if not reason or not reason.strip():
        raise ValueError(
            "remove_decision requires a non-empty 'reason' (audit-trail obligation)"
        )

    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    if not await decision_exists(client, decision_id):
        raise ValueError(f"No decision row for {decision_id}")

    rows = await client.query(
        f"SELECT signoff FROM {decision_id} LIMIT 1",
    )
    existing_signoff = (rows[0].get("signoff") if rows else None) or None

    # Idempotent fast path — already removed
    if (
        existing_signoff
        and isinstance(existing_signoff, dict)
        and existing_signoff.get("state") == "removed"
    ):
        projected = await project_decision_status(client, decision_id)
        return RemoveDecisionResponse(
            decision_id=decision_id,
            was_new=False,
            signoff=existing_signoff,
            projected_status=projected,
        )

    head_ref = getattr(ctx, "authoritative_sha", "") or ""
    session_id = getattr(ctx, "session_id", None) or ""
    now_iso = datetime.now(UTC).isoformat()
    previous_state = (
        existing_signoff.get("state")
        if isinstance(existing_signoff, dict)
        else None
    )

    signoff = {
        "state": "removed",
        "signer": signer,
        "session_id": session_id,
        "removed_at": now_iso,
        "previous_state": previous_state,
        "reason": reason,
        "source_commit_ref": head_ref,
    }

    await client.query(
        f"UPDATE {decision_id} SET signoff = $signoff",
        {"signoff": signoff},
    )
    projected = await project_decision_status(client, decision_id)
    await update_decision_status(client, decision_id, projected)

    # Emit decision_removed.completed event when team mode is active.
    # In local-only mode (no _writer on the adapter), audit-log obligation
    # is satisfied by the ledger row's signoff history itself.
    writer = getattr(ledger, "_writer", None)
    if writer is not None:
        writer.write(
            "decision_removed.completed",
            {
                "decision_id": decision_id,
                "signoff": signoff,
            },
        )

    logger.info(
        "[remove_decision] decision=%s signer=%s previous_state=%s projected_status=%s",
        decision_id,
        signer,
        previous_state,
        projected,
    )

    return RemoveDecisionResponse(
        decision_id=decision_id,
        was_new=True,
        signoff=signoff,
        projected_status=projected,
    )
