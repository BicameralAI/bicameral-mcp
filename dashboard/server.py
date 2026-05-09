"""Asyncio HTTP sidecar for the live dashboard.

Binds to a free local port in the existing event loop, serves the compiled
dashboard HTML, and streams live HistoryResponse updates via SSE.

Endpoints:
  GET /           → dashboard.html (self-contained HTML/JS bundle)
  GET /events     → SSE stream; each event is a full HistoryResponse JSON blob
  GET /history    → one-shot JSON dump (initial load fallback)

The server is a module-level singleton started once when serve_stdio() runs.
Write handlers (ingest, link_commit) call notify(ctx) to push updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ASSETS_DIR = Path(__file__).parent.parent / "assets"
_PORT_FILE = Path.home() / ".bicameral" / "dashboard.port"

_HTTP_200_HTML = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\n"
_HTTP_200_JSON = "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\nCache-Control: no-store\r\nAccess-Control-Allow-Origin: *\r\n"
_HTTP_200_SSE = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: keep-alive\r\nAccess-Control-Allow-Origin: *\r\n\r\n"
_HTTP_404 = "HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot Found"
_HTTP_500 = "HTTP/1.1 500 Internal Server Error\r\nContent-Length: 5\r\n\r\nError"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _read_port_file() -> int | None:
    try:
        return int(_PORT_FILE.read_text().strip())
    except Exception:
        return None


def _write_port_file(port: int) -> None:
    _PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PORT_FILE.write_text(str(port))


def _send_body(headers: str, body: bytes) -> bytes:
    full = headers + f"Content-Length: {len(body)}\r\n\r\n"
    return full.encode() + body


class DashboardServer:
    """Minimal asyncio HTTP server. Runs as a background task in the MCP event loop."""

    def __init__(self) -> None:
        self._port: int = 0
        self._server: asyncio.AbstractServer | None = None
        self._ctx_factory: Any = None  # callable() → BicameralContext

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://localhost:{self._port}"

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self, ctx_factory: Any) -> None:
        """Bind to a free port and start serving. No-op if already running."""
        if self._server is not None:
            return
        self._ctx_factory = ctx_factory
        self._port = _find_free_port()
        self._server = await asyncio.start_server(
            self._handle_connection,
            "127.0.0.1",
            self._port,
        )
        _write_port_file(self._port)
        logger.info("[dashboard] HTTP sidecar listening on %s", self.url)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def notify(self, ctx: Any) -> None:
        """Build a fresh HistoryResponse and push it to all SSE clients."""
        from dashboard.sse import get_broadcaster

        broadcaster = get_broadcaster()
        if broadcaster.subscriber_count == 0:
            return
        try:
            from handlers.history import handle_history

            response = await handle_history(ctx)
            payload = json.dumps(response.model_dump(), default=str)
            await broadcaster.broadcast(payload)
        except Exception as exc:
            logger.warning("[dashboard] notify failed: %s", exc)

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.read(4096)
            if not raw:
                writer.close()
                return
            first_line = raw.split(b"\r\n", 1)[0].decode(errors="replace")
            parts = first_line.split()
            method = parts[0] if parts else ""
            path = parts[1].split("?")[0] if len(parts) > 1 else "/"

            if method == "GET" and path == "/":
                await self._serve_html(writer)
            elif method == "GET" and path == "/history":
                await self._serve_history(writer)
            elif method == "GET" and path == "/events":
                await self._serve_sse(writer)
            else:
                writer.write(_HTTP_404.encode())
                await writer.drain()
        except Exception as exc:
            logger.debug("[dashboard] connection error: %s", exc)
            try:
                writer.write(_HTTP_500.encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _serve_html(self, writer: asyncio.StreamWriter) -> None:
        html_path = _ASSETS_DIR / "dashboard.html"
        try:
            body = html_path.read_bytes()
        except FileNotFoundError:
            body = b"<html><body><h1>Dashboard not built yet.</h1><p>Run: make dashboard</p></body></html>"
        writer.write(_send_body(_HTTP_200_HTML, body))
        await writer.drain()

    async def _serve_history(self, writer: asyncio.StreamWriter) -> None:
        try:
            ctx = self._ctx_factory()
            from handlers.history import handle_history

            response = await handle_history(ctx)
            body = json.dumps(response.model_dump(), default=str).encode()
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
        writer.write(_send_body(_HTTP_200_JSON, body))
        await writer.drain()

    async def _serve_sse(self, writer: asyncio.StreamWriter) -> None:
        from dashboard.sse import get_broadcaster

        broadcaster = get_broadcaster()
        writer.write(_HTTP_200_SSE.encode())
        await writer.drain()

        # Push the current state immediately on connect
        try:
            ctx = self._ctx_factory()
            from handlers.history import handle_history

            response = await handle_history(ctx)
            initial = json.dumps(response.model_dump(), default=str)
            writer.write(f"data: {initial}\n\n".encode())
            await writer.drain()
        except Exception as exc:
            logger.debug("[dashboard] SSE initial push failed: %s", exc)

        q = broadcaster.subscribe()
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=30.0)
                except TimeoutError:
                    # Keep connection alive with an SSE comment; loop and keep waiting.
                    writer.write(b": keepalive\n\n")
                    await writer.drain()
                    continue
                if data is None:
                    break
                writer.write(f"data: {data}\n\n".encode())
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            broadcaster.unsubscribe(q)


_server: DashboardServer | None = None


def get_dashboard_server() -> DashboardServer:
    global _server
    if _server is None:
        _server = DashboardServer()
    return _server


async def notify_dashboard(ctx: Any) -> None:
    """Convenience function called by write handlers after each commit."""
    srv = get_dashboard_server()
    if not srv.running:
        return
    await srv.notify(ctx)
