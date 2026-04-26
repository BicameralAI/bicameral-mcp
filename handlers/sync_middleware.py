"""Session-sync middleware.

Single entry point:

- ``ensure_ledger_synced(ctx)`` — lazy HEAD catch-up. Keeps the ledger current
  without requiring an explicit link_commit call before every tool.

Safe to call from any handler; swallows all exceptions so it never blocks.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def ensure_ledger_synced(ctx) -> None:
    """Sync ledger to HEAD if it has moved since the last sync.

    Runs the lazy HEAD catch-up so preflight/history always see fresh
    compliance state. All exceptions are swallowed.
    """
    sync_state = getattr(ctx, "_sync_state", None)

    try:
        from handlers.link_commit import handle_link_commit, _read_current_head_sha
        live_head = _read_current_head_sha(getattr(ctx, "repo_path", "") or ".")
        if live_head and live_head != (sync_state or {}).get("last_sync_sha"):
            await handle_link_commit(ctx, "HEAD")
            logger.debug("[sync_middleware] catch-up ran for %s", live_head[:8])
    except Exception as exc:
        logger.debug("[sync_middleware] catch-up failed: %s", exc)
