"""UDS + JSON-RPC server stub for the universal protocol.

Phase 1 ships the dispatch surface; Phase 2 wires concrete handlers
(ingest/egress routers, grounding port) when the daemon process is
extracted. The server today is only used by the conformance test suite and
by the in-tree round-trip tests for the protocol package itself.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any, Awaitable, Callable

from .contracts import PROTOCOL_VERSION, ProtocolError
from .transport import make_error, make_response, read_message, write_message

Handler = Callable[[dict[str, Any]], Awaitable[Any]]


class ProtocolServer:
    """Asyncio UDS server with a JSON-RPC method-dispatch table.

    Registered handlers receive the raw `params` dict and return any value
    JSON-serializable. The built-in ``system.version`` method always
    responds with the server's ``PROTOCOL_VERSION``.
    """

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path
        self._methods: dict[str, Handler] = {}
        self._server: asyncio.AbstractServer | None = None
        self.register("system.version", self._handle_version)

    @staticmethod
    async def _handle_version(_: dict[str, Any]) -> str:
        return PROTOCOL_VERSION

    def register(self, method: str, handler: Handler) -> None:
        self._methods[method] = handler

    async def start(self) -> None:
        # Defensive: remove a stale socket file from a previous run.
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path.exists():
            self._socket_path.unlink()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            while True:
                try:
                    msg = await read_message(reader)
                except ProtocolError:
                    return
                await self._handle_message(msg, writer)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_message(
        self, msg: dict[str, Any], writer: asyncio.StreamWriter
    ) -> None:
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(method, str):
            await write_message(writer, make_error(req_id, -32600, "missing method"))
            return
        handler = self._methods.get(method)
        if handler is None:
            await write_message(writer, make_error(req_id, -32601, f"unknown method {method}"))
            return
        try:
            result = handler(params)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 — boundary serializes any handler error
            await write_message(writer, make_error(req_id, -32000, str(exc)))
            return
        await write_message(writer, make_response(req_id, result))
