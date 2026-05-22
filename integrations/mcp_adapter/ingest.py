"""MCP-side IngestAdapter shell.

Phase 2b: stub responses that exercise the registry → runtime → adapter
dispatch path without yet writing to the ledger. Phase 2c replaces the
bodies with calls into the moved ``daemon/ledger/`` writer (and the
expanded protocol surface adds the read/write methods that today's MCP
handlers use directly).
"""

from __future__ import annotations

from protocol.contracts import (
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
)


class MCPIngestAdapter:
    """Registers as IngestAdapter named ``mcp``."""

    name = "mcp"

    async def ingest(self, req: IngestRequest) -> IngestResult:
        # Phase 2c wires the real ledger write. For now, accept + echo
        # a deterministic stub id so the round-trip path is exercisable
        # end-to-end in tests and during dev-time daemon smoke checks.
        return IngestResult(
            status="accepted",
            decision_ids=[f"stub-{req.source_id}"],
            reason=None,
        )

    async def link_commit(self, _req: LinkCommitRequest) -> LinkCommitResult:
        return LinkCommitResult(status="no_change", regions_updated=0)
