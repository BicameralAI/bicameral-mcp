"""Typed RPC client for the universal ingest/egress/grounding surface.

Reads the daemon socket path from ``~/.bicameral/daemon.json`` (unless
overridden via the constructor). A single connection multiplexes calls;
requests are matched to responses by ``id``.

Today this lives in-tree alongside the MCP server. Phase 3 extracts it to
``bicameral-protocol`` so any adapter (Linear, Notion, future grounders)
imports the same client without dragging in MCP code.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

from .contracts import (
    PROTOCOL_VERSION,
    AnalyzeRegionRequest,
    BatchAnalyzeRequest,
    DriftResult,
    ExtractSymbolsRequest,
    GetNeighborsRequest,
    IngestRequest,
    IngestResult,
    LinkCommitRequest,
    LinkCommitResult,
    Neighbor,
    NotificationEvent,
    DeliveryResult,
    ProtocolError,
    ProtocolVersionError,
    Symbol,
    ValidateSymbolsRequest,
)
from .transport import make_request, read_message, write_message


def _parse_major(version: str) -> int:
    return int(version.split(".", 1)[0])


def _default_socket_path() -> Path:
    return Path.home() / ".bicameral" / "daemon.sock"


class ProtocolClient:
    """Async RPC client for the bicameral daemon.

    Usage::

        client = ProtocolClient()
        await client.connect()
        symbols = await client.validate_symbols(req)
        await client.close()
    """

    def __init__(self, socket_path: Path | None = None) -> None:
        self._socket_path = socket_path or self._read_daemon_socket()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._id_iter = itertools.count(1)
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    @staticmethod
    def _read_daemon_socket() -> Path:
        config = Path.home() / ".bicameral" / "daemon.json"
        if not config.exists():
            return _default_socket_path()
        data = json.loads(config.read_text(encoding="utf-8"))
        return Path(data["socket_path"])

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(
            str(self._socket_path)
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._verify_version()

    async def _verify_version(self) -> None:
        server_version = await self._call("system.version", {})
        if not isinstance(server_version, str):
            raise ProtocolError("system.version did not return a string")
        if _parse_major(server_version) != _parse_major(PROTOCOL_VERSION):
            raise ProtocolVersionError(
                f"client {PROTOCOL_VERSION} incompatible with server {server_version}"
            )

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closed:
                msg = await read_message(self._reader)
                self._dispatch_response(msg)
        except ProtocolError:
            self._fail_pending(ProtocolError("connection closed"))
        except asyncio.CancelledError:
            self._fail_pending(ProtocolError("client cancelled"))
            raise

    def _dispatch_response(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        if not isinstance(req_id, int):
            return
        future = self._pending.pop(req_id, None)
        if future is None or future.done():
            return
        if "error" in msg:
            future.set_exception(ProtocolError(msg["error"].get("message", "error")))
        else:
            future.set_result(msg.get("result"))

    def _fail_pending(self, exc: Exception) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        if self._writer is None:
            raise ProtocolError("client not connected")
        req_id = next(self._id_iter)
        future: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future
        await write_message(self._writer, make_request(req_id, method, params))
        return await future

    async def close(self) -> None:
        self._closed = True
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, ProtocolError):
                pass

    # ── Typed adapter facade ─────────────────────────────────────────

    async def ingest(self, req: IngestRequest) -> IngestResult:
        result = await self._call("ingest.ingest", req.model_dump())
        return IngestResult.model_validate(result)

    async def link_commit(self, req: LinkCommitRequest) -> LinkCommitResult:
        result = await self._call("ingest.link_commit", req.model_dump())
        return LinkCommitResult.model_validate(result)

    async def deliver(self, event: NotificationEvent) -> DeliveryResult:
        result = await self._call("egress.deliver", event.model_dump())
        return DeliveryResult.model_validate(result)

    async def validate_symbols(
        self, req: ValidateSymbolsRequest
    ) -> list[Symbol]:
        result = await self._call("grounding.validate_symbols", req.model_dump())
        return [Symbol.model_validate(item) for item in result]

    async def extract_symbols(
        self, req: ExtractSymbolsRequest
    ) -> list[Symbol]:
        result = await self._call("grounding.extract_symbols", req.model_dump())
        return [Symbol.model_validate(item) for item in result]

    async def get_neighbors(
        self, req: GetNeighborsRequest
    ) -> list[Neighbor]:
        result = await self._call("grounding.get_neighbors", req.model_dump())
        return [Neighbor.model_validate(item) for item in result]

    async def analyze_region(
        self, req: AnalyzeRegionRequest
    ) -> DriftResult:
        result = await self._call("grounding.analyze_region", req.model_dump())
        return DriftResult.model_validate(result)

    async def batch_analyze_regions(
        self, req: BatchAnalyzeRequest
    ) -> list[DriftResult]:
        result = await self._call(
            "grounding.batch_analyze_regions", req.model_dump()
        )
        return [DriftResult.model_validate(item) for item in result]
