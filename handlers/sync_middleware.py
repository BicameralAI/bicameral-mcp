"""Session-sync middleware (v0.6.1).

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

from contracts import SessionStartBanner

logger = logging.getLogger(__name__)


async def get_session_start_banner(ctx) -> SessionStartBanner | None:
    """Return a drifted-decisions banner on the first MCP call of a session.

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
        drifted = await ctx.ledger.get_decisions_by_status(["drifted"])
        if not drifted:
            sync_state["session_banner"] = None
            return None
        banner = SessionStartBanner(
            drifted_count=len(drifted),
            items=[
                {
                    "decision_id": d.get("decision_id", ""),
                    "description": d.get("description", ""),
                    "source_ref": d.get("source_ref", ""),
                }
                for d in drifted
            ],
            message=(
                f"Session start: {len(drifted)} drifted decision(s) — "
                "code has changed since these were last verified. "
                "Review before implementing in affected areas."
            ),
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
