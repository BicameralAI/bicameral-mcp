"""Handler for /bicameral.ratify MCP tool — v0.7.1.

Supports two actions:
  - ratify (default): promotes signoff from proposed → ratified
  - reject: records explicit rejection, steers agents away from implementing

Both actions are idempotent: calling with the same target state is a no-op
that returns the existing signoff with was_new=False.

No unratify. Rescinding ratification or rejection requires writing a new
decision that supersedes the previous one — clean audit trail, no rollback.

Phase 2c-6c: split into facade (handle_ratify) + pure impl (_handle_ratify_impl).
The daemon's ``write.ratify`` dispatcher calls ``_handle_ratify_impl`` directly;
the MCP-side facade routes through ``ctx.daemon.ratify`` when available.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from contracts import RatifyResponse
from ledger.queries import decision_exists, project_decision_status
from preflight_telemetry import telemetry_enabled, write_engagement
from protocol.categorization import write_tool

logger = logging.getLogger(__name__)


async def _handle_ratify_impl(
    ctx,
    decision_id: str,
    signer: str,
    note: str = "",
    action: str = "ratify",
    *,
    preflight_id: str | None = None,
) -> dict[str, Any]:
    """Core ratify logic — ledger mutation, telemetry emit.

    Invoked by the daemon's ``write.ratify`` protocol handler and by the
    MCP-side facade when the daemon is not reachable.
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

    if (
        existing_signoff
        and isinstance(existing_signoff, dict)
        and existing_signoff.get("state") == target_state
    ):
        projected = await project_decision_status(client, decision_id)
        if telemetry_enabled():
            write_engagement(
                session_id=str(getattr(ctx, "session_id", "unknown") or "unknown"),
                tool="bicameral.ratify",
                decision_id=decision_id,
                preflight_id=preflight_id,
                file_paths=None,
            )
        return RatifyResponse(
            decision_id=decision_id,
            was_new=False,
            signoff=existing_signoff,
            projected_status=projected,
            preflight_id=preflight_id,
        ).model_dump()

    head_ref = getattr(ctx, "authoritative_sha", "") or ""
    session_id = getattr(ctx, "session_id", None) or ""
    now_iso = datetime.now(UTC).isoformat()

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
        decision_id,
        action,
        signer,
        projected,
    )

    if telemetry_enabled():
        write_engagement(
            session_id=str(getattr(ctx, "session_id", "unknown") or "unknown"),
            tool="bicameral.ratify",
            decision_id=decision_id,
            preflight_id=preflight_id,
            file_paths=None,
        )

    return RatifyResponse(
        decision_id=decision_id,
        was_new=True,
        signoff=signoff,
        projected_status=projected,
        preflight_id=preflight_id,
    ).model_dump()


@write_tool("write.ratify")
async def handle_ratify(
    ctx,
    decision_id: str,
    signer: str,
    note: str = "",
    action: str = "ratify",
    *,
    preflight_id: str | None = None,
) -> RatifyResponse:
    """Set signoff on a decision.

    action='ratify' (default): proposed → ratified. Drift tracking activates.
    action='reject': records explicit rejection. The decision stays in the
    ledger as a negative signal — agents consult it to avoid implementing
    decisions the product team has explicitly rejected.

    Idempotent: calling with the same action on an already-finalized decision
    returns was_new=False and leaves the existing signoff untouched.

    Phase 2c-6c: if ``ctx.daemon`` is reachable, routes through the daemon's
    single-writer queue. Falls through to ``_handle_ratify_impl`` otherwise.
    """
    daemon = getattr(ctx, "daemon", None)

    if daemon is not None:
        try:
            from protocol.contracts import RatifyResult

            repo_id = getattr(ctx, "repo_id", None) or "local"
            raw = await daemon.ratify(
                repo_id=repo_id,
                decision_id=decision_id,
                signer=signer,
                note=note,
                action=action,
                preflight_id=preflight_id,
            )
            result = RatifyResult.model_validate(raw)
            return RatifyResponse(
                decision_id=result.decision_id,
                was_new=result.was_new,
                signoff=result.signoff,
                projected_status=result.projected_status,  # type: ignore[arg-type]
                preflight_id=None,
            )
        except Exception:
            logger.debug(
                "[handle_ratify] daemon call failed, falling through to in-process impl",
                exc_info=True,
            )

    raw = await _handle_ratify_impl(
        ctx=ctx,
        decision_id=decision_id,
        signer=signer,
        note=note,
        action=action,
        preflight_id=preflight_id,
    )
    return RatifyResponse.model_validate(raw)
