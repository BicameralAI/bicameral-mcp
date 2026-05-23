"""UDS + JSON-RPC server stub for the universal protocol.

Phase 1 shipped the dispatch surface; Phase 2b adds connection-scoped
tenant binding via the ``system.attach`` RPC. Handlers that need a
tenant_id receive it from the ``ConnectionContext`` rather than from the
request payload.

Phase 2c will wire concrete handlers (ingest/egress routers, grounding
port) when the daemon process is extracted. The server today is used by
the conformance test suite and by the in-tree round-trip tests for the
protocol package itself.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .contracts import (
    PROTOCOL_VERSION,
    AttachRequest,
    AttachResult,
    ConnectionContext,
    NotAttachedError,
    ProtocolError,
)
from .transport import make_error, make_response, read_message, write_message

Handler = Callable[..., Awaitable[Any] | Any]


# Methods that are allowed before ``system.attach`` lands a tenant on the
# connection. Everything else requires attach first.
_PRE_ATTACH_ALLOWED = frozenset({"system.version", "system.attach"})


class ProtocolServer:
    """Asyncio UDS server with a JSON-RPC method-dispatch table.

    Registered handlers may take either ``(params)`` or ``(params, ctx)`` —
    the dispatcher introspects the signature and passes ``ConnectionContext``
    when the handler expects it. Handlers may return any JSON-serializable
    value. The built-in ``system.version`` / ``system.attach`` methods are
    always registered.
    """

    def __init__(self, socket_path: Path) -> None:
        self._socket_path = socket_path
        self._methods: dict[str, Handler] = {}
        self._server: asyncio.AbstractServer | None = None
        self.register("system.version", self._handle_version)
        self.register("system.attach", self._handle_attach)

    @staticmethod
    async def _handle_version(_: dict[str, Any]) -> str:
        return PROTOCOL_VERSION

    @staticmethod
    async def _handle_attach(
        params: dict[str, Any], ctx: ConnectionContext
    ) -> dict[str, Any]:
        req = AttachRequest.model_validate(params)
        ctx.tenant_id = req.tenant_id
        ctx.user_id = req.user_id
        return AttachResult(tenant_id=req.tenant_id).model_dump()

    def register(self, method: str, handler: Handler) -> None:
        self._methods[method] = handler

    async def start(self) -> None:
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
        ctx = ConnectionContext()
        try:
            while True:
                try:
                    msg = await read_message(reader)
                except ProtocolError:
                    return
                await self._handle_message(msg, writer, ctx)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_message(
        self,
        msg: dict[str, Any],
        writer: asyncio.StreamWriter,
        ctx: ConnectionContext,
    ) -> None:
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(method, str):
            await write_message(writer, make_error(req_id, -32600, "missing method"))
            return
        if method not in _PRE_ATTACH_ALLOWED and not ctx.attached:
            await write_message(
                writer,
                make_error(req_id, -32002, "session not attached; call system.attach first"),
            )
            return
        handler = self._methods.get(method)
        if handler is None:
            await write_message(writer, make_error(req_id, -32601, f"unknown method {method}"))
            return
        try:
            result = self._invoke(handler, params, ctx)
            if inspect.isawaitable(result):
                result = await result
        except NotAttachedError as exc:
            await write_message(writer, make_error(req_id, -32002, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001 — boundary serializes any handler error
            await write_message(writer, make_error(req_id, -32000, str(exc)))
            return
        await write_message(writer, make_response(req_id, result))

    @staticmethod
    def _invoke(handler: Handler, params: dict[str, Any], ctx: ConnectionContext) -> Any:
        sig = inspect.signature(handler)
        if len(sig.parameters) >= 2:
            return handler(params, ctx)
        return handler(params)
