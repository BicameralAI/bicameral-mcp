"""Handler for /bicameral.remove_decision MCP tool.

Hard-delete (v0.15.x, decision:i4wafafzowm3ai5eyhgs): physically removes
the decision row and all references to it (binds_to / yields / supersedes /
context_for / about edges + compliance_check cache rows). A
decision_removed.completed event captures the full pre-deletion snapshot
in the event journal — the "soft audit trail" that replaces the prior
tombstone-row model.

Why hard-delete: soft-delete was intended as a negative-signal mechanism
(rows with signoff.state="removed" warn future agents away from
re-introducing the same wrong decision). In practice the dominant call
shape is janitorial — test pollution, accidentally-ingested rows,
retracted ideas with no learning value — where tombstones become friction
that surfaces in preflight, occupies dashboard slots, and gets re-bound
by drift sweeps. Supersession (record a new decision contradicting an old
one) remains the right tool when you DO want a persistent negative
signal.

Audit obligation:
  - ``reason`` is required (empty/whitespace string raises ValueError).
  - A decision_removed.completed event is emitted to the event log when
    the ledger has an attached writer (team mode). Local-only mode skips
    the event emission. The event payload carries the full pre-deletion
    snapshot so the action is recoverable from the journal alone.

Idempotent:
  - Calling on a missing ``decision_id`` returns ``was_new=False`` and
    ``event_logged=False`` without raising. The matching event in the
    journal is the canonical record of any prior removal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from contracts import RemoveDecisionResponse
from ledger.queries import decision_exists

logger = logging.getLogger(__name__)


async def handle_remove_decision(
    ctx,
    decision_id: str,
    signer: str,
    reason: str,
) -> RemoveDecisionResponse:
    """Hard-delete a decision and all references to it.

    Returns ``was_new=True`` on the first call (row + edges + cache rows
    deleted; event emitted in team mode). Returns ``was_new=False`` on
    subsequent calls because the row is no longer present — the event
    journal already records the original removal.
    """
    if not reason or not reason.strip():
        raise ValueError("remove_decision requires a non-empty 'reason' (audit-trail obligation)")

    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    # Idempotent fast path — row is already gone. The matching
    # decision_removed.completed event in the journal is the canonical
    # record of prior removal; we don't try to look it up here.
    if not await decision_exists(client, decision_id):
        return RemoveDecisionResponse(
            decision_id=decision_id,
            was_new=False,
            event_logged=False,
            removed_at=None,
            previous_state=None,
            reason=reason,
        )

    # Snapshot the row + signoff BEFORE delete so the event payload
    # captures enough state to recover from the journal alone.
    snapshot_rows = await client.query(
        f"SELECT type::string(id) AS id, description, status, source_type, "
        f"source_ref, signoff, decision_level, parent_decision_id, "
        f"feature_group, governance, created_at, updated_at "
        f"FROM {decision_id} LIMIT 1"
    )
    snapshot = snapshot_rows[0] if snapshot_rows else {}
    existing_signoff = snapshot.get("signoff") or None
    previous_state = existing_signoff.get("state") if isinstance(existing_signoff, dict) else None

    session_id = getattr(ctx, "session_id", None) or ""
    head_ref = getattr(ctx, "authoritative_sha", "") or ""
    now_iso = datetime.now(UTC).isoformat()

    await _hard_delete_decision(client, decision_id)

    event_logged = False
    writer = getattr(ledger, "_writer", None)
    if writer is not None:
        from events.dogfood import maybe_dogfood_label

        payload = maybe_dogfood_label(
            {
                "decision_id": decision_id,
                "signer": signer,
                "reason": reason,
                "removed_at": now_iso,
                "session_id": session_id,
                "previous_state": previous_state,
                "source_commit_ref": head_ref,
                # Full pre-deletion snapshot — recoverable audit trail.
                "snapshot": {
                    "description": snapshot.get("description", ""),
                    "status": snapshot.get("status", ""),
                    "source_type": snapshot.get("source_type", ""),
                    "source_ref": snapshot.get("source_ref", ""),
                    "decision_level": snapshot.get("decision_level"),
                    "parent_decision_id": snapshot.get("parent_decision_id"),
                    "feature_group": snapshot.get("feature_group"),
                    "governance": snapshot.get("governance"),
                    "signoff": existing_signoff,
                    "created_at": str(snapshot.get("created_at", "")),
                    "updated_at": str(snapshot.get("updated_at", "")),
                },
            }
        )
        writer.write("decision_removed.completed", payload)
        event_logged = True

    logger.info(
        "[remove_decision] hard-delete decision=%s signer=%s previous_state=%s event_logged=%s",
        decision_id,
        signer,
        previous_state,
        event_logged,
    )

    return RemoveDecisionResponse(
        decision_id=decision_id,
        was_new=True,
        event_logged=event_logged,
        removed_at=now_iso,
        previous_state=previous_state,
        reason=reason,
    )


async def _hard_delete_decision(client, decision_id: str) -> None:
    """Physically remove a decision row and every reference to it.

    Removed:
      - binds_to edges OUT of this decision (→ code_region).
      - yields edges IN to this decision (input_span →).
      - supersedes edges in either direction.
      - context_for edges IN to this decision (input_span →).
      - about edges OUT of this decision (→ code_subject).
      - compliance_check rows keyed on this decision_id.
      - the decision row itself.

    Children orphaned cleanly: ``decision.parent_decision_id`` is set to
    NONE on any decision that pointed at this id, so they become
    root-level decisions instead of dangling pointers.
    """
    # NULL out child pointers so hierarchical decisions don't dangle.
    await client.query(
        f"UPDATE decision SET parent_decision_id = NONE WHERE parent_decision_id = '{decision_id}'"
    )
    # Delete every edge touching this decision (one query per edge table
    # — SurrealDB v2 has no cascade and the IN/OUT-typed RELATION tables
    # can't be combined in a single DELETE statement).
    await client.query(f"DELETE binds_to WHERE in = {decision_id}")
    await client.query(f"DELETE yields WHERE out = {decision_id}")
    await client.query(f"DELETE supersedes WHERE in = {decision_id} OR out = {decision_id}")
    await client.query(f"DELETE context_for WHERE out = {decision_id}")
    await client.query(f"DELETE about WHERE in = {decision_id}")
    # Drop the compliance_check verdict cache for this decision.
    await client.query("DELETE compliance_check WHERE decision_id = $d", {"d": decision_id})
    # Finally, the row itself.
    await client.query(f"DELETE {decision_id}")
