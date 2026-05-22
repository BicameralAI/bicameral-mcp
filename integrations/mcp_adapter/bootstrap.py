"""Bootstrap entry — boots a Supervisor with the MCP adapter registered.

Phase 2b ships this as a callable that tests + dev-time daemon checks
can invoke; Phase 2c wires it into ``server.py`` startup so every MCP
process either spawns or attaches to the user-scoped daemon.
"""

from __future__ import annotations

from pathlib import Path

from daemon.registry import AdapterRegistry
from daemon.supervisor import Supervisor

from .egress import MCPEgressAdapter
from .ingest import MCPIngestAdapter


async def bootstrap_mcp_daemon(
    socket_path: Path | None = None,
    descriptor_path: Path | None = None,
) -> Supervisor:
    """Build + start a Supervisor with the MCP IngestAdapter and
    EgressAdapter pre-registered.

    Returns the running Supervisor; caller is responsible for
    ``await supervisor.stop()`` on teardown.
    """
    registry = AdapterRegistry()
    registry.register_ingest(MCPIngestAdapter())
    registry.register_egress(MCPEgressAdapter())
    supervisor = Supervisor(
        registry=registry,
        socket_path=socket_path,
        descriptor_path=descriptor_path,
    )
    await supervisor.start()
    return supervisor
