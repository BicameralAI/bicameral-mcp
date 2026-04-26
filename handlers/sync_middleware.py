"""Session-sync middleware (v0.7.0).

Two entry points:

- ``ensure_ledger_synced(ctx)`` — sync + banner. Use in handlers that don't
  already call ``handle_link_commit`` themselves (preflight, history).

- ``get_session_start_banner(ctx)`` — banner only. Use in handlers that
  already call ``handle_link_commit`` for sync (search_decisions).

Both are safe to call concurrently and swallow all exceptions — they must
never block a handler.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from contracts import SessionStartBanner

logger = logging.getLogger(__name__)

_STALE_PROPOSAL_DAYS = 14


def _is_stale_proposal(decision: dict) -> bool:
    """Return True if this proposal's signoff.created_at is >14 days old."""
    if decision.get("status") != "proposal":
        return False
    signoff = decision.get("signoff") or {}
    if not isinstance(signoff, dict):
        return False
    created_at_str = signoff.get("created_at", "")
    if not created_at_str:
        return False
    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - created_at).days
        return age_days >= _STALE_PROPOSAL_DAYS
    except Exception:
        return False


async def get_session_start_banner(ctx) -> SessionStartBanner | None:
    """Return an open-items banner on the first MCP call of a session.

    Surfaces drifted, ungrounded, and stale proposals (>14 days idle).
    Sets ``_sync_state["session_started"]`` to True on first call so
    subsequent calls within the same server session return None immediately.
    The banner is cached in ``_sync_state["session_banner"]`` so a second
    handler that also calls this in the same request sees the same object.
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return None

    if sync_state.get("session_started", False):
        return None

    sync_state["session_started"] = True

    # Return a previously-computed banner (e.g. ensure_ledger_synced ran first).
    if "session_banner" in sync_state:
        return sync_state["session_banner"]

    try:
        open_items = await ctx.ledger.get_decisions_by_status(
            ["drifted", "ungrounded", "proposal", "context_pending"]
        )
        if not open_items:
            sync_state["session_banner"] = None
            return None

        drifted_count = sum(1 for d in open_items if d.get("status") == "drifted")
        ungrounded_count = sum(1 for d in open_items if d.get("status") == "ungrounded")
        proposal_count = sum(1 for d in open_items if d.get("status") == "proposal")
        stale_proposal_count = sum(1 for d in open_items if _is_stale_proposal(d))
        context_pending_count = sum(1 for d in open_items if d.get("status") == "context_pending")

        # context_pending always surfaces (needs a business driver answer).
        # Proposals only surface when stale — normal proposals are expected noise.
        has_attention_items = bool(drifted_count or ungrounded_count or stale_proposal_count or context_pending_count)
        if not has_attention_items:
            sync_state["session_banner"] = None
            return None

        max_items = 10
        # Priority: drifted → context_pending (needs answer) → stale proposals → ungrounded
        def _sort_key(d: dict) -> int:
            s = d.get("status", "")
            if s == "drifted":
                return 0
            if s == "context_pending":
                return 1
            if s == "proposal" and _is_stale_proposal(d):
                return 2
            return 3

        sorted_items = sorted(open_items, key=_sort_key)[:max_items]
        truncated = len(open_items) > max_items

        items = []
        for d in sorted_items:
            item: dict = {
                "decision_id": d.get("decision_id", ""),
                "description": d.get("description", ""),
                "source_ref": d.get("source_ref", ""),
                "status": d.get("status", ""),
            }
            if d.get("status") == "context_pending":
                signoff = d.get("signoff") or {}
                q = signoff.get("context_question", "") if isinstance(signoff, dict) else ""
                if q:
                    item["context_question"] = q
            items.append(item)

        parts = []
        if drifted_count:
            parts.append(f"{drifted_count} drifted")
        if context_pending_count:
            parts.append(f"{context_pending_count} awaiting business context")
        if ungrounded_count:
            parts.append(f"{ungrounded_count} ungrounded")
        if stale_proposal_count:
            parts.append(f"{stale_proposal_count} stale proposal(s) need review")
        summary = " + ".join(parts)
        overflow = f" (showing top {max_items})" if truncated else ""
        message = (
            f"Session start: {summary} decision(s){overflow} — review before "
            "implementing in affected areas."
        )

        banner = SessionStartBanner(
            drifted_count=drifted_count,
            ungrounded_count=ungrounded_count,
            proposal_count=proposal_count,
            stale_proposal_count=stale_proposal_count,
            context_pending_count=context_pending_count,
            items=items,
            truncated=truncated,
            message=message,
        )
        sync_state["session_banner"] = banner
        return banner
    except Exception as exc:
        logger.debug("[sync_middleware] session banner query failed: %s", exc)
        return None


async def ensure_ledger_synced(ctx) -> SessionStartBanner | None:
    """Sync ledger to HEAD and return a session-start banner if applicable.

    Runs the same lazy HEAD catch-up that preflight used to inline, then
    delegates to ``get_session_start_banner``.  All exceptions are swallowed.
    """
    sync_state = getattr(ctx, "_sync_state", None)
    if not isinstance(sync_state, dict):
        return await get_session_start_banner(ctx)

    try:
        from handlers.link_commit import handle_link_commit, _read_current_head_sha
        live_head = _read_current_head_sha(getattr(ctx, "repo_path", "") or ".")
        if live_head and live_head != sync_state.get("last_sync_sha"):
            await handle_link_commit(ctx, "HEAD")
            logger.debug("[sync_middleware] catch-up ran for %s", live_head[:8])
    except Exception as exc:
        logger.debug("[sync_middleware] catch-up failed: %s", exc)

    return await get_session_start_banner(ctx)
