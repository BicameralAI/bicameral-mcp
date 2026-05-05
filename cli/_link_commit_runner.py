"""Sync wrapper around handle_link_commit. Shared by branch-scan and
link_commit CLI subcommands. Lazy-imports SurrealDB-touching modules
so callers don't pay the import cost when no ledger is configured.

Promoted from cli/branch_scan.py (#48) to a shared module under #124
when the link_commit CLI subcommand was added.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from contracts import LinkCommitResponse


def invoke_link_commit(commit_hash: str = "HEAD") -> LinkCommitResponse | None:
    """Drive the async ``handle_link_commit`` from sync context.

    Returns ``None`` when:
      - ``~/.bicameral/ledger.db`` does not exist (no configured ledger), OR
      - the underlying handler raises (graceful skip — caller decides on
        loud vs. silent failure semantics).
    """
    if not (Path.home() / ".bicameral" / "ledger.db").exists():
        return None
    from context import BicameralContext
    from handlers.link_commit import handle_link_commit

    async def _run() -> LinkCommitResponse:
        ctx = BicameralContext.from_env()
        return await handle_link_commit(ctx, commit_hash=commit_hash)

    try:
        return asyncio.run(_run())
    except Exception:  # noqa: BLE001 — caller decides loud vs. silent
        return None
