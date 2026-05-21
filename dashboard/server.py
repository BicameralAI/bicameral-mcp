"""Asyncio HTTP sidecar for the live dashboard.

Binds to a free local port in the existing event loop, serves the compiled
dashboard HTML, and streams live HistoryResponse updates via SSE.

Endpoints:
  GET /           → dashboard.html (self-contained HTML/JS bundle)
  GET /events     → SSE stream; each event is a full HistoryResponse JSON blob
  GET /history    → one-shot JSON dump (initial load fallback)
  GET /pulse      → one-shot ProjectPulseSummary JSON (#437 Phase 3)

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
            elif method == "GET" and path == "/pulse":
                await self._serve_pulse(writer)
            elif method == "GET" and path == "/events":
                await self._serve_sse(writer)
            elif method == "POST" and path == "/admin/query":
                # #278 Phase 3 — off-by-default admin SurrealQL panel.
                await self._serve_admin_query(writer, raw)
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

    async def _serve_pulse(self, writer: asyncio.StreamWriter) -> None:
        """Serve a one-shot Project Pulse summary as JSON (#437 Phase 3).

        Read-only — mirrors ``_serve_history``: builds a ``ProjectPulseSummary``
        from the ledger via ``build_project_pulse`` and returns its
        ``to_dict()`` shape. ``build_project_pulse`` is fail-soft per section;
        a hard failure here surfaces an ``{"error": ...}`` body so the Pulse
        section can show an error without crashing the dashboard server.
        """
        try:
            ctx = self._ctx_factory()
            from pulse.summary import build_project_pulse

            summary = await build_project_pulse(ctx.ledger)
            body = json.dumps(summary.to_dict(), default=str).encode()
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
        writer.write(_send_body(_HTTP_200_JSON, body))
        await writer.drain()

    async def _serve_admin_query(self, writer: asyncio.StreamWriter, raw: bytes) -> None:
        """Dispatch a POST /admin/query request to the admin module.

        Parses the Origin header + JSON body from the raw HTTP request,
        delegates to ``dashboard.admin.process_admin_query``, and writes
        the JSON response back. The admin module enforces the env-flag
        gates, origin check, signer requirement, and audit-log emission.
        """
        from dashboard.admin import process_admin_query

        # Parse headers to extract Origin
        head, _, body = raw.partition(b"\r\n\r\n")
        origin: str | None = None
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"origin:"):
                origin = line.split(b":", 1)[1].decode(errors="replace").strip()
                break

        try:
            payload_in = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload_in = {}

        ctx = self._ctx_factory()
        status, response_body = await process_admin_query(
            payload_in=payload_in,
            origin=origin,
            dashboard_port=self._port,
            ledger=ctx.ledger,
            repo_path=getattr(ctx, "repo_path", "."),
        )

        body_bytes = json.dumps(response_body, default=str).encode()
        # No CORS allow-origin on admin responses (Phase 3 Discipline #3).
        status_line = {
            200: "HTTP/1.1 200 OK",
            400: "HTTP/1.1 400 Bad Request",
            403: "HTTP/1.1 403 Forbidden",
            404: "HTTP/1.1 404 Not Found",
        }.get(status, "HTTP/1.1 500 Internal Server Error")
        headers = (
            f"{status_line}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Cache-Control: no-store\r\n"
            f"Content-Length: {len(body_bytes)}\r\n\r\n"
        )
        writer.write(headers.encode() + body_bytes)
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
