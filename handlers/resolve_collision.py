"""Handler for bicameral.resolve_collision MCP tool — v0.8.0.

Dual-mode HITL resolution tool:

  Collision mode  — called when ingest surfaced supersession_candidates:
    resolve_collision(new_id, old_id, action='supersede'|'keep_both')
    - supersede:  RELATE new→supersedes→old, mark old as 'superseded',
                  clear collision_pending on new so it enters normal flow.
    - keep_both:  clear collision_pending on new; no supersedes edge written.

  Context-for mode — called when ingest surfaced context_for_candidates:
    resolve_collision(span_id, decision_id, confirmed=True|False)
    - confirmed:  RELATE span→context_for→decision (state='confirmed').
    - rejected:   RELATE span→context_for→decision (state='rejected').
      Both writes are recorded to prevent re-surfacing on future ingests.

Decision.status is NEVER changed directly by this tool. It is recomputed via
project_decision_status (the double-entry authority) after each action.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from contracts import ResolveCollisionResponse
from ledger.queries import (
    decision_exists,
    project_decision_status,
    relate_context_for,
    relate_supersedes,
    update_decision_status,
)

logger = logging.getLogger(__name__)


async def handle_resolve_collision(
    ctx,
    # Collision mode params
    new_id: str | None = None,
    old_id: str | None = None,
    action: str | None = None,  # 'supersede' | 'keep_both'
    # Context-for mode params
    span_id: str | None = None,
    decision_id: str | None = None,
    confirmed: bool | None = None,
) -> ResolveCollisionResponse:
    """Resolve a collision or context_for candidate surfaced during ingest."""
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    _session_id = getattr(ctx, "session_id", None) or ""
    _now_iso = datetime.now(UTC).isoformat()

    # ── Collision mode ────────────────────────────────────────────────────
    if action is not None:
        if not new_id or not old_id:
            raise ValueError("collision mode requires new_id and old_id")
        if action not in ("supersede", "keep_both", "link_parent"):
            raise ValueError(
                f"action must be 'supersede', 'keep_both', or 'link_parent', got {action!r}"
            )

        if not await decision_exists(client, new_id):
            raise ValueError(f"No decision row for new_id={new_id}")

        if action == "supersede":
            if not await decision_exists(client, old_id):
                raise ValueError(f"No decision row for old_id={old_id}")

            # Write supersedes edge (idempotent)
            await relate_supersedes(
                client,
                new_id,
                old_id,
                confidence=1.0,
                reason=f"human-confirmed supersession via resolve_collision session={_session_id}",
            )

            # Mark old decision as superseded in signoff (not status).
            # Supersession is a human editorial decision, not a code-compliance observation.
            # The old decision's status field retains its last code-compliance value
            # and is frozen — drift sweeps skip decisions where signoff.state='superseded'.
            # Merge with existing signoff so a prior ratification record is preserved.
            _existing_rows = await client.query(f"SELECT signoff FROM {old_id} LIMIT 1")
            _old_signoff: dict = {}
            if _existing_rows and isinstance(_existing_rows[0], dict):
                _old_signoff = _existing_rows[0].get("signoff") or {}
            await client.execute(
                f"UPDATE {old_id} SET signoff = $s",
                {
                    "s": {
                        **_old_signoff,
                        "state": "superseded",
                        "superseded_by": new_id,
                        "superseded_at": _now_iso,
                        "session_id": _session_id,
                    }
                },
            )
            old_status = "superseded"

            logger.info("[resolve_collision] supersede: %s supersedes %s", new_id, old_id)

        elif action == "link_parent":
            # Cross-level parent-child link: write parent_decision_id on the child (new_id).
            # old_id is the parent (higher-level decision, e.g. L1).
            # No supersedes edge, no status change — purely structural.
            if not await decision_exists(client, old_id):
                raise ValueError(f"No decision row for old_id={old_id}")
            await client.execute(
                f"UPDATE {new_id} SET parent_decision_id = $pid",
                {"pid": old_id},
            )
            logger.info(
                "[resolve_collision] link_parent: %s.parent_decision_id = %s", new_id, old_id
            )
            new_status = await project_decision_status(client, new_id)
            await update_decision_status(client, new_id, new_status)
            return ResolveCollisionResponse(
                mode="collision",
                action_taken="link_parent",
                new_decision_id=new_id,
                old_decision_id=old_id,
                edge_written=True,
                new_status=new_status,
                old_status="",
            )

        else:  # keep_both
            old_status = ""
            logger.info("[resolve_collision] keep_both: %s and %s both remain", new_id, old_id)

        # Clear collision_pending on new decision so it enters normal flow
        _proposed_signoff = {
            "state": "proposed",
            "session_id": _session_id,
            "created_at": _now_iso,
        }
        await client.execute(
            f"UPDATE {new_id} SET signoff = $s",
            {"s": _proposed_signoff},
        )
        new_status = await project_decision_status(client, new_id)
        await update_decision_status(client, new_id, new_status)

        return ResolveCollisionResponse(
            mode="collision",
            action_taken=action,
            new_decision_id=new_id,
            old_decision_id=old_id,
            edge_written=(action == "supersede"),
            new_status=new_status,
            old_status=old_status,
        )

    # ── Context-for mode ──────────────────────────────────────────────────
    if confirmed is not None:
        if not span_id or not decision_id:
            raise ValueError("context_for mode requires span_id and decision_id")

        state = "confirmed" if confirmed else "rejected"
        await relate_context_for(
            client,
            span_id,
            decision_id,
            state=state,
            relevance_score=0.0,
            reason=f"human-{state} via resolve_collision session={_session_id}",
        )

        logger.info(
            "[resolve_collision] context_for: span=%s decision=%s state=%s",
            span_id,
            decision_id,
            state,
        )

        return ResolveCollisionResponse(
            mode="context_for",
            action_taken=state,
            span_id=span_id,
            decision_id=decision_id,
            edge_written=True,
            new_status="context_pending",
        )

    raise ValueError(
        "resolve_collision requires either action= (collision mode) "
        "or confirmed= (context_for mode)"
    )
