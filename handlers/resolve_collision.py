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

Phase 2c-6c: split into facade (handle_resolve_collision) + pure impl
(_handle_resolve_collision_impl). The daemon's ``write.resolve_collision``
dispatcher calls ``_handle_resolve_collision_impl`` directly; the MCP-side
facade routes through ``ctx.daemon.resolve_collision`` when available.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from contracts import ResolveCollisionResponse
from ledger.queries import (
    decision_exists,
    project_decision_status,
    relate_context_for,
    update_decision_status,
)
from protocol.categorization import write_tool

logger = logging.getLogger(__name__)


async def _handle_resolve_collision_impl(
    ctx,
    # Collision mode params
    new_id: str | None = None,
    old_id: str | None = None,
    action: str | None = None,  # 'supersede' | 'keep_both'
    # Context-for mode params
    span_id: str | None = None,
    decision_id: str | None = None,
    confirmed: bool | None = None,
) -> dict[str, Any]:
    """Core resolve_collision logic — ledger mutation.

    Invoked by the daemon's ``write.resolve_collision`` protocol handler and by
    the MCP-side facade when the daemon is not reachable.
    """
    result = await _handle_resolve_collision_core(
        ctx=ctx,
        new_id=new_id,
        old_id=old_id,
        action=action,
        span_id=span_id,
        decision_id=decision_id,
        confirmed=confirmed,
    )
    return result.model_dump()


async def _handle_resolve_collision_core(
    ctx,
    new_id: str | None = None,
    old_id: str | None = None,
    action: str | None = None,
    span_id: str | None = None,
    decision_id: str | None = None,
    confirmed: bool | None = None,
) -> ResolveCollisionResponse:
    """Internal core — returns a ResolveCollisionResponse object.

    Both ``_handle_resolve_collision_impl`` and ``handle_resolve_collision``
    delegate here to share the full body without duplication.
    """
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

            # Routes through TeamWriteAdapter when in team mode so the
            # supersession is emitted as a decision_superseded.completed
            # event. The adapter handles edge creation + frozen-signoff
            # merge so the old decision's prior ratification record is
            # preserved (drift sweeps skip signoff.state='superseded').
            result = await ledger.apply_supersede(
                new_id=new_id,
                old_id=old_id,
                signer=_session_id,
                signoff_note="",
                superseded_at=_now_iso,
                session_id=_session_id,
            )
            old_status = result.get("old_status", "superseded")

            logger.info("[resolve_collision] supersede: %s supersedes %s", new_id, old_id)

        elif action == "link_parent":
            # Cross-level parent-child link: write parent_decision_id on the child (new_id).
            # old_id is the parent (higher-level decision, e.g. L1).
            # No supersedes edge, no status change — purely structural.
            if not await decision_exists(client, old_id):
                raise ValueError(f"No decision row for old_id={old_id}")
            await client.execute(
                f"UPDATE {new_id} SET parent_decision_id = $pid, updated_at = time::now()",
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
            f"UPDATE {new_id} SET signoff = $s, updated_at = time::now()",
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


@write_tool("write.resolve_collision")
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
    """Resolve a collision or context_for candidate surfaced during ingest.

    Phase 2c-6c: if ``ctx.daemon`` is reachable, routes through the daemon's
    single-writer queue. Falls through to ``_handle_resolve_collision_impl``
    (via ``_handle_resolve_collision_core``) otherwise.
    """
    daemon = getattr(ctx, "daemon", None)

    if daemon is not None:
        try:
            from protocol.contracts import ResolveCollisionResult

            repo_id = getattr(ctx, "repo_id", None) or "local"
            raw = await daemon.resolve_collision(
                repo_id=repo_id,
                new_id=new_id,
                old_id=old_id,
                action=action,
                span_id=span_id,
                decision_id=decision_id,
                confirmed=confirmed,
            )
            result = ResolveCollisionResult.model_validate(raw)
            return ResolveCollisionResponse(
                mode=result.mode,  # type: ignore[arg-type]
                action_taken=result.action_taken,
                new_decision_id=result.new_decision_id,
                old_decision_id=result.old_decision_id,
                span_id=result.span_id,
                decision_id=result.decision_id,
                edge_written=result.edge_written,
                new_status=result.new_status,
                old_status=result.old_status,
            )
        except Exception:
            logger.debug(
                "[handle_resolve_collision] daemon call failed, falling through to in-process impl",
                exc_info=True,
            )

    return await _handle_resolve_collision_core(
        ctx=ctx,
        new_id=new_id,
        old_id=old_id,
        action=action,
        span_id=span_id,
        decision_id=decision_id,
        confirmed=confirmed,
    )
