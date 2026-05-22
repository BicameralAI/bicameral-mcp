"""In-process runtime: hosts the ProtocolServer + AdapterRegistry.

The runtime is the *thing inside* the daemon process. It registers all
default RPC methods (`ingest.*`, `egress.*`, `grounding.*`, `system.*`)
against the registry, then runs the server until ``stop()`` is called.

Phase 2b: adapters now receive a ConnectionContext alongside their request
so they can scope per tenant. Phase 2c will spawn + supervise the surreal
child process and wire concrete handlers; today the runtime hosts the
protocol surface and dispatches to registered adapter shells.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol.contracts import (
    ConnectionContext,
    IngestRequest,
    LinkCommitRequest,
    NotificationEvent,
)
from protocol.server import ProtocolServer

from .registry import AdapterRegistry


class Runtime:
    def __init__(self, socket_path: Path, registry: AdapterRegistry) -> None:
        self._registry = registry
        self._server = ProtocolServer(socket_path)
        self._register_default_methods()

    @property
    def server(self) -> ProtocolServer:
        return self._server

    @property
    def registry(self) -> AdapterRegistry:
        return self._registry

    def _register_default_methods(self) -> None:
        self._server.register("ingest.ingest", self._handle_ingest)
        self._server.register("ingest.link_commit", self._handle_link_commit)
        self._server.register("egress.deliver", self._handle_deliver)
        # grounding.* methods land in Phase 2c when the code-locator + drift
        # analyzer move into daemon/grounding/. system.version + system.attach
        # are registered by ProtocolServer itself.

    async def _handle_ingest(
        self, params: dict[str, Any], ctx: ConnectionContext
    ) -> dict[str, Any]:
        req = IngestRequest.model_validate(params)
        adapter = self._registry.lookup_ingest(req.adapter_name)
        result = await adapter.ingest(req, ctx)
        return result.model_dump()

    async def _handle_link_commit(
        self, params: dict[str, Any], ctx: ConnectionContext
    ) -> dict[str, Any]:
        req = LinkCommitRequest.model_validate(params)
        # link_commit dispatches to ANY ingest adapter that opted into commit
        # binding — for v0.1 we route by repo_id convention; the active MCP
        # adapter handles all commits in its repo. Phase 2c generalizes.
        ingest_names = self._registry.ingest_names()
        if not ingest_names:
            raise RuntimeError("no ingest adapters registered for link_commit")
        adapter = self._registry.lookup_ingest(ingest_names[0])
        result = await adapter.link_commit(req, ctx)
        return result.model_dump()

    async def _handle_deliver(
        self, params: dict[str, Any], ctx: ConnectionContext
    ) -> dict[str, Any]:
        # The egress channel is selected by the embedded channel name.
        channel = params.pop("channel", None)
        if not isinstance(channel, str):
            raise ValueError("egress.deliver requires a 'channel' string")
        event = NotificationEvent.model_validate(params)
        adapter = self._registry.lookup_egress(channel)
        result = await adapter.deliver(event, ctx)
        return result.model_dump()

    async def start(self) -> None:
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()
