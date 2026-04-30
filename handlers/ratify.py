"""Handler for /bicameral.ratify MCP tool — v0.7.1.

Supports two actions:
  - ratify (default): promotes signoff from proposed → ratified
  - reject: records explicit rejection, steers agents away from implementing

Both actions are idempotent: calling with the same target state is a no-op
that returns the existing signoff with was_new=False.

No unratify. Rescinding ratification or rejection requires writing a new
decision that supersedes the previous one — clean audit trail, no rollback.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import RatifyResponse
from ledger.queries import decision_exists, project_decision_status
# triage-adapt: dropped preflight_telemetry import from auto-merge — module
# is on dev (#65 preflight telemetry) but not on triage; the cherry-picked
# body doesn't actually reference it (intent of e6d4b8f for this file is
# routing through ledger.apply_ratify, not adding telemetry)

logger = logging.getLogger(__name__)


async def handle_ratify(
    ctx,
    decision_id: str,
    signer: str,
    note: str = "",
    action: str = "ratify",
) -> RatifyResponse:
    """Set signoff on a decision.

    action='ratify' (default): proposed → ratified. Drift tracking activates.
    action='reject': records explicit rejection. The decision stays in the
    ledger as a negative signal — agents consult it to avoid implementing
    decisions the product team has explicitly rejected.

    Idempotent: calling with the same action on an already-finalized decision
    returns was_new=False and leaves the existing signoff untouched.
    """
    if action not in ("ratify", "reject"):
        raise ValueError(f"Unknown action '{action}'; must be 'ratify' or 'reject'")

    target_state = "ratified" if action == "ratify" else "rejected"

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

    if existing_signoff and isinstance(existing_signoff, dict) and existing_signoff.get("state") == target_state:
        projected = await project_decision_status(client, decision_id)
        return RatifyResponse(
            decision_id=decision_id,
            was_new=False,
            signoff=existing_signoff,
            projected_status=projected,
        )

    head_ref = getattr(ctx, "authoritative_sha", "") or ""
    session_id = getattr(ctx, "session_id", None) or ""
    now_iso = datetime.now(timezone.utc).isoformat()

    if action == "ratify":
        signoff = {
            "state": "ratified",
            "signer": signer,
            "session_id": session_id,
            "ratified_at": now_iso,
            "source_commit_ref": head_ref,
            "note": note,
        }
    else:
        signoff = {
            "state": "rejected",
            "signer": signer,
            "session_id": session_id,
            "rejected_at": now_iso,
            "source_commit_ref": head_ref,
            "note": note,
        }

    # Routes through TeamWriteAdapter when in team mode so the signoff
    # change is emitted as a decision_ratified.completed event.
    projected = await ledger.apply_ratify(decision_id, signoff)

    logger.info(
        "[ratify] decision=%s action=%s signer=%s projected_status=%s",
        decision_id, action, signer, projected,
    )

    return RatifyResponse(
        decision_id=decision_id,
        was_new=True,
        signoff=signoff,
        projected_status=projected,
    )
