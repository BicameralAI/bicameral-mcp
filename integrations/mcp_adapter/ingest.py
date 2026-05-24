"""MCP-side IngestAdapter — real ledger writes via _impl functions.

Phase 2c-6b: replaces the stub bodies with calls into the existing
``handlers.ingest._handle_ingest_impl`` and
``handlers.link_commit._handle_link_commit_impl`` so the daemon
subprocess performs the same ledger mutations as the in-process path.

The adapter receives an ``IngestRequest`` (wire payload) and a
``ConnectionContext`` (per-connection tenant/user identity). It resolves
a ``BicameralContext`` via ``BicameralContext.from_env()`` (same shim as
``protocol/handlers/reads.py`` — multi-repo resolution is Phase 2c-3+).

The lazy import pattern (imports inside the method bodies) mirrors
``protocol/handlers/reads.py`` and ``protocol/handlers/writes.py`` to
avoid the infinite-loop bug surfaced in #509: importing the facade at
module level would trigger adapter registration at import time and
create a circular dependency.
"""

from __future__ import annotations

import json as _json

from protocol.contracts import (
    ConnectionContext,
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
)


class MCPIngestAdapter:
    """Registers as IngestAdapter named ``mcp``."""

    name = "mcp"

    async def ingest(self, req: IngestRequest, ctx: ConnectionContext) -> IngestResult:
        """Delegate to ``_handle_ingest_impl`` with a BicameralContext from env."""
        # Phase 2c-6b: lazy import to avoid circular-import / infinite-loop
        # (facade → daemon → adapter → facade → …). Same pattern as
        # protocol/handlers/reads.py handle_read_history.
        from context import BicameralContext
        from handlers.ingest import _handle_ingest_impl

        bctx = BicameralContext.from_env()
        payload = _json.loads(req.payload)
        raw = await _handle_ingest_impl(bctx, payload, ingest_mode=req.mode)
        decision_ids = [d.decision_id for d in (raw.created_decisions or [])]
        return IngestResult(
            status="accepted" if raw.ingested else "refused",
            decision_ids=decision_ids,
            reason=None,
        )

    async def link_commit(
        self, req: LinkCommitRequest, _ctx: ConnectionContext
    ) -> LinkCommitResult:
        """Delegate to ``_handle_link_commit_impl`` with a BicameralContext from env."""
        # Phase 2c-6b: lazy import to avoid circular-import / infinite-loop.
        from context import BicameralContext
        from handlers.link_commit import _handle_link_commit_impl

        bctx = BicameralContext.from_env()
        raw = await _handle_link_commit_impl(bctx, req.commit_sha)
        return LinkCommitResult(
            status="linked" if raw.synced else "no_change",
            regions_updated=raw.regions_updated,
        )
