"""MCP-side IngestAdapter shell.

Phase 2b: stub responses that exercise the registry → runtime → adapter
dispatch path including the ConnectionContext (tenant_id from the
connection's ``system.attach`` lands on every call). Phase 2c replaces
the bodies with calls into the moved ``daemon/ledger/`` writer (and the
expanded protocol surface adds the read/write methods that today's MCP
handlers use directly). The stub IDs surface ``ctx.tenant_id`` so that
tenant-scoping is verifiable in tests today.
"""

from __future__ import annotations

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
        # Phase 2c wires the real ledger write. For now, accept + echo
        # a deterministic stub id that includes the tenant so tests can
        # verify the tenant_id is threading correctly through dispatch.
        return IngestResult(
            status="accepted",
            decision_ids=[f"stub-{ctx.tenant_id}-{req.source_id}"],
            reason=None,
        )

    async def link_commit(
        self, _req: LinkCommitRequest, _ctx: ConnectionContext
    ) -> LinkCommitResult:
        return LinkCommitResult(status="no_change", regions_updated=0)
