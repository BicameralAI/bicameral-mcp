"""Bootstrap entry — boots a Supervisor with the MCP adapter registered.

Phase 2b: ``tenant_id`` defaults to ``"local"`` per the connection-scoped
tenant model. Hosted mode passes the gateway-resolved tenant identity
when the daemon process itself is multi-tenant; for now, ``tenant_id``
is wired through to the storage layout (Phase 2c uses it to compute
``~/.bicameral/tenants/<tenant_id>/projects/<repo_id>/``).

Phase 2c wires this into ``server.py`` startup so every MCP process
either spawns or attaches to the user-scoped daemon.
"""

from __future__ import annotations

from pathlib import Path

from daemon.registry import AdapterRegistry
from daemon.supervisor import Supervisor
from protocol.contracts import LOCAL_TENANT_ID

from .egress import MCPEgressAdapter
from .ingest import MCPIngestAdapter


async def bootstrap_mcp_daemon(
    socket_path: Path | None = None,
    descriptor_path: Path | None = None,
    tenant_id: str = LOCAL_TENANT_ID,
) -> Supervisor:
    """Build + start a Supervisor with the MCP IngestAdapter and
    EgressAdapter pre-registered.

    ``tenant_id`` defaults to ``"local"``. Local-mode daemons always run
    as a single-tenant process and don't need to vary this; hosted-mode
    setup will pass the per-tenant identity here when the multi-tenant
    daemon is brought up in Phase 5.

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
    supervisor.default_tenant_id = tenant_id  # captured for future Phase 2c use
    await supervisor.start()
    return supervisor
