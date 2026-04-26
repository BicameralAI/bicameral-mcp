"""Session-sync middleware.

Single entry point:

- ``ensure_ledger_synced(ctx)`` — lazy HEAD catch-up. Keeps the ledger current
  without requiring an explicit link_commit call before every tool.

Called at the top of every tool dispatch in server.py (except link_commit
itself). Uses a process-level SHA cache so the link_commit DB+git work only
runs when HEAD has actually moved. Safe to call concurrently; swallows all
exceptions so it never blocks a handler.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Process-level cache: survives across call_tool invocations within the same
# server process. Avoids re-running link_commit when HEAD hasn't moved.
_LAST_SYNCED_SHA: str | None = None


async def ensure_ledger_synced(ctx) -> None:
    """Sync ledger to HEAD if it has moved since the last sync in this process.

    Calls handle_link_commit only when the live HEAD SHA differs from
    _LAST_SYNCED_SHA. Idempotent and exception-safe.
    """
    global _LAST_SYNCED_SHA

    try:
        from handlers.link_commit import handle_link_commit, _read_current_head_sha
        live_head = _read_current_head_sha(getattr(ctx, "repo_path", "") or ".")
        if live_head and live_head != _LAST_SYNCED_SHA:
            await handle_link_commit(ctx, "HEAD")
            _LAST_SYNCED_SHA = live_head
            logger.debug("[sync_middleware] catch-up ran for %s", live_head[:8])
    except Exception as exc:
        logger.debug("[sync_middleware] catch-up failed: %s", exc)
