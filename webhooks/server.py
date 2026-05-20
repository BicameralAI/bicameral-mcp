"""Asyncio HTTP webhook receiver (#337 cycle 5).

Separate process from the MCP stdio server. Operator runs:

    bicameral-mcp webhook-server [--port 8765] [--host 127.0.0.1]

Default bind is loopback only — operator MUST pass ``--allow-public``
to bind to any non-loopback address (signals they've put TLS in front
via reverse proxy / tunnel). The receiver listens on plain HTTP; we do
NOT terminate TLS ourselves.

Routes:
- ``POST /webhooks/github``  → ``webhooks.github.handle``
- ``POST /webhooks/slack``   → ``webhooks.slack.handle``
- ``POST /webhooks/notion``  → ``webhooks.notion.handle``
- ``GET /health``            → 200 ack (for load-balancer health checks)

## Hardening (post code-review)

- 60s total per-request wall-clock budget (slow-loris mitigation).
- Max 50 concurrent connections (resource-exhaustion mitigation).
- Max 100 headers, 16 KiB total header bytes (header-flood mitigation).
- 8 MiB body cap, checked on Content-Length BEFORE reading.
- Reject duplicate Content-Length, any Transfer-Encoding (smuggling).
- Reject negative or non-int Content-Length, CR/LF in header values.
- Header values .decode("ascii", errors="strict") — non-ASCII rejected
  rather than silently substituted.

The server is deliberately single-threaded asyncio. Per-request work
that touches the ledger or the network is dispatched via
``asyncio.to_thread`` so the event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import ipaddress
import sys
from types import MappingProxyType

_MAX_REQUEST_BYTES = 8 * 1024 * 1024  # 8 MiB
_READ_TIMEOUT_SECONDS = 30.0
_TOTAL_REQUEST_TIMEOUT_SECONDS = 60.0
_MAX_CONCURRENT_REQUESTS = 50
_MAX_HEADERS = 100
_MAX_HEADER_BYTES = 16 * 1024  # 16 KiB total header bytes

# Loopback addresses that don't need --allow-public.
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


_request_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily build the concurrency limiter on the active event loop."""
    global _request_semaphore
    if _request_semaphore is None:
        _request_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REQUESTS)
    return _request_semaphore


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """One HTTP request: bounded resources, single dispatch, respond, close."""
    sem = _get_semaphore()
    if sem.locked():
        # All slots taken — reject with 503 instead of queueing forever.
        try:
            await _respond(writer, 503, "server busy")
        finally:
            await _close(writer)
        return
    async with sem:
        try:
            await asyncio.wait_for(
                _process_request(reader, writer),
                timeout=_TOTAL_REQUEST_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            try:
                await _respond(writer, 408, "request timeout (total budget exceeded)")
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            print(f"[webhook-server] unhandled error: {exc}", file=sys.stderr)
            try:
                await _respond(writer, 500, "internal error")
            except Exception:  # noqa: BLE001
                pass
        finally:
            await _close(writer)


async def _process_request(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Parse one HTTP request and dispatch. Outer wrapper applies timeout."""
    # ── Request line ─────────────────────────────────────────────────────
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_SECONDS)
    except asyncio.IncompleteReadError:
        return
    if not request_line:
        return
    try:
        line = request_line.decode("ascii", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError:
        await _respond(writer, 400, "non-ASCII in request line")
        return
    # RFC 7230 §3.1.1: exactly one SP between method, URI, version.
    parts = line.split(" ")
    if len(parts) != 3:
        await _respond(writer, 400, "malformed request line")
        return
    method, path, _version = parts

    # ── Headers ──────────────────────────────────────────────────────────
    headers: dict[str, str] = {}
    header_byte_total = 0
    header_count = 0
    seen_content_length = False
    while True:
        try:
            line_b = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT_SECONDS)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await _respond(writer, 431, "header read failure")
            return
        if line_b in (b"\r\n", b"\n", b""):
            break
        header_count += 1
        header_byte_total += len(line_b)
        if header_count > _MAX_HEADERS or header_byte_total > _MAX_HEADER_BYTES:
            await _respond(writer, 431, "header limits exceeded")
            return
        try:
            raw = line_b.decode("ascii", errors="strict").rstrip("\r\n")
        except UnicodeDecodeError:
            await _respond(writer, 400, "non-ASCII in headers")
            return
        # Reject embedded CR/LF in the decoded value — newline injection
        # protection (even after the rstrip).
        if "\r" in raw or "\n" in raw:
            await _respond(writer, 400, "embedded newline in header")
            return
        name, sep, value = raw.partition(":")
        if not sep:
            await _respond(writer, 400, "malformed header")
            return
        key = name.strip().lower()
        val = value.strip()
        # Smuggling defense: reject any Transfer-Encoding header outright;
        # reject duplicate Content-Length.
        if key == "transfer-encoding":
            await _respond(writer, 400, "Transfer-Encoding not supported")
            return
        if key == "content-length":
            if seen_content_length:
                await _respond(writer, 400, "duplicate Content-Length")
                return
            seen_content_length = True
        headers[key] = val

    # ── Routes ───────────────────────────────────────────────────────────
    if method == "GET" and path == "/health":
        await _respond(writer, 200, "ok")
        return
    if method != "POST":
        await _respond(writer, 405, "method not allowed")
        return

    # ── Body ─────────────────────────────────────────────────────────────
    raw_cl = headers.get("content-length", "0")
    try:
        content_length = int(raw_cl)
    except ValueError:
        await _respond(writer, 400, "invalid Content-Length")
        return
    if content_length < 0:
        await _respond(writer, 400, "negative Content-Length")
        return
    if content_length > _MAX_REQUEST_BYTES:
        await _respond(writer, 413, f"body exceeds {_MAX_REQUEST_BYTES} byte cap")
        return
    body = b""
    if content_length > 0:
        try:
            body = await asyncio.wait_for(
                reader.readexactly(content_length), timeout=_READ_TIMEOUT_SECONDS
            )
        except asyncio.IncompleteReadError:
            await _respond(writer, 400, "body shorter than Content-Length")
            return

    # ── Dispatch ─────────────────────────────────────────────────────────
    if path == "/webhooks/github":
        # Freeze headers as a read-only view before handing to the worker
        # thread — defense against future regressions where the handler
        # mutates the dict.
        status, message = await asyncio.to_thread(
            _dispatch_github, body, MappingProxyType(dict(headers))
        )
        await _respond(writer, status, message)
        return

    if path == "/webhooks/slack":
        # Slack handler returns (status, body, content_type) — H1 review
        # finding: server passes content type through verbatim instead of
        # guessing by inspecting the body.
        status, message, content_type = await asyncio.to_thread(
            _dispatch_slack, body, MappingProxyType(dict(headers))
        )
        await _respond(writer, status, message, content_type=content_type)
        return

    if path == "/webhooks/notion":
        status, message = await asyncio.to_thread(
            _dispatch_notion, body, MappingProxyType(dict(headers))
        )
        await _respond(writer, status, message)
        return

    await _respond(writer, 404, "not found")


def _dispatch_github(body: bytes, headers) -> tuple[int, str]:
    """Sync entrypoint into the GitHub handler (called via to_thread)."""
    from webhooks.github import handle

    event = headers.get("x-github-event", "")
    delivery = headers.get("x-github-delivery", "")
    signature = headers.get("x-hub-signature-256")
    return handle(
        event=event,
        delivery_id=delivery,
        body=body,
        signature_header=signature,
    )


def _dispatch_notion(body: bytes, headers) -> tuple[int, str]:
    """Sync entrypoint into the Notion handler (called via to_thread)."""
    from webhooks.notion import handle

    return handle(
        body=body,
        signature_header=headers.get("x-notion-signature"),
    )


def _dispatch_slack(body: bytes, headers) -> tuple[int, str, str]:
    """Sync entrypoint into the Slack handler (called via to_thread).

    Returns ``(status, body, content_type)`` — content type is explicit
    rather than guessed from body shape (H1 review finding).
    """
    from webhooks.slack import handle

    timestamp = headers.get("x-slack-request-timestamp")
    signature = headers.get("x-slack-signature")
    return handle(
        body=body,
        timestamp_header=timestamp,
        signature_header=signature,
    )


async def _respond(
    writer: asyncio.StreamWriter,
    status: int,
    message: str,
    *,
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    """Write a minimal HTTP/1.0 response and flush."""
    reason = {
        200: "OK",
        400: "Bad Request",
        401: "Unauthorized",
        404: "Not Found",
        405: "Method Not Allowed",
        408: "Request Timeout",
        413: "Payload Too Large",
        422: "Unprocessable Entity",
        431: "Request Header Fields Too Large",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }.get(status, "Unknown")
    # For JSON content (e.g. Slack url_verification challenge echo) we
    # don't append a trailing newline — preserves byte-exact response.
    if content_type.startswith("application/json"):
        body = message.encode("utf-8")
    else:
        body = (message + "\n").encode("utf-8")
    response = (
        f"HTTP/1.0 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Cache-Control: no-store\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii") + body
    writer.write(response)
    await writer.drain()


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:  # noqa: BLE001
        pass


def _is_loopback(host: str) -> bool:
    if host in _LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    allow_public: bool = False,
) -> None:
    """Bind + serve until interrupted.

    Raises ``RuntimeError`` if ``host`` is non-loopback and
    ``allow_public`` is False — operator must explicitly opt into
    public bind to acknowledge the TLS-termination responsibility.
    """
    if not _is_loopback(host) and not allow_public:
        raise RuntimeError(
            f"refusing to bind {host}: non-loopback bind requires --allow-public. "
            "Public binds MUST be behind a TLS-terminating reverse proxy / tunnel; "
            "the receiver itself listens on plain HTTP."
        )
    server = await asyncio.start_server(handle_client, host, port)
    addr = server.sockets[0].getsockname() if server.sockets else (host, port)
    print(
        f"[webhook-server] listening on {addr[0]}:{addr[1]}. "
        "PUBLIC BIND: operator must terminate TLS at a reverse proxy in front. "
        "Dedup cache is process-local — multi-process deployments need a shared "
        "cache (Redis, etc.) to share dedup state across workers."
        if not _is_loopback(addr[0])
        else f"[webhook-server] listening on {addr[0]}:{addr[1]} (loopback). "
        "Dedup cache is process-local.",
        file=sys.stderr,
    )
    async with server:
        await server.serve_forever()
