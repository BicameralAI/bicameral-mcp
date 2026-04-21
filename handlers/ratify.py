"""Handler for /bicameral.ratify MCP tool — v0.5.0.

Sets product_signoff on a decision node (one-shot, idempotent).
Calling ratify on an already-signed-off decision is a no-op that
returns the existing signoff record with was_new=False.

No unratify tool. Rescinding signoff requires writing a new decision
that supersedes the previous one — clean audit trail, no hidden rollback.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import RatifyResponse
from ledger.queries import decision_exists, project_decision_status, update_decision_status

logger = logging.getLogger(__name__)


async def handle_ratify(
    ctx,
    decision_id: str,
    signer: str,
    note: str = "",
) -> RatifyResponse:
    """Flip product_signoff from None to a populated object.

    Idempotent: calling ratify on an already-signed-off decision returns
    was_new=False and leaves the existing signoff record untouched.

    Writes {signer, timestamp, source_commit_ref, note} to
    decision.product_signoff, then recomputes status via
    project_decision_status.
    """
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    if not await decision_exists(client, decision_id):
        raise ValueError(f"No decision row for {decision_id}")

    # Check if already ratified
    rows = await client.query(
        f"SELECT product_signoff FROM {decision_id} LIMIT 1",
    )
    existing_signoff = (rows[0].get("product_signoff") if rows else None) or None

    if existing_signoff:
        # Already ratified — idempotent no-op
        projected = await project_decision_status(client, decision_id)
        return RatifyResponse(
            decision_id=decision_id,
            was_new=False,
            product_signoff=existing_signoff,
            projected_status=projected,
        )

    # Build the signoff object
    head_ref = getattr(ctx, "authoritative_sha", "") or ""
    signoff = {
        "signer": signer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_commit_ref": head_ref,
        "note": note,
    }

    await client.query(
        f"UPDATE {decision_id} SET product_signoff = $signoff",
        {"signoff": signoff},
    )

    projected = await project_decision_status(client, decision_id)
    await update_decision_status(client, decision_id, projected)

    logger.info(
        "[ratify] decision=%s signer=%s projected_status=%s",
        decision_id, signer, projected,
    )

    return RatifyResponse(
        decision_id=decision_id,
        was_new=True,
        product_signoff=signoff,
        projected_status=projected,
    )
