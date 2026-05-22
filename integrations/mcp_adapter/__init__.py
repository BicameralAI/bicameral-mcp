"""MCP-side adapter — the bridge between the bicameral MCP stdio server
and the daemon.

Phase 2b (this commit): adapter shells that satisfy the IngestAdapter /
EgressAdapter Protocols and register cleanly with the daemon's
AdapterRegistry. Real ledger + notification wiring lands in Phase 2c —
once the protocol surface is expanded to cover ratify/supersede/query
and the ledger module physically moves into ``daemon/``.

The bootstrap entry point — ``bootstrap_mcp_daemon`` — is callable
today; server.py wires it in during Phase 2c.
"""

from __future__ import annotations

from .bootstrap import bootstrap_mcp_daemon
from .egress import MCPEgressAdapter
from .ingest import MCPIngestAdapter

__all__ = [
    "MCPEgressAdapter",
    "MCPIngestAdapter",
    "bootstrap_mcp_daemon",
]
