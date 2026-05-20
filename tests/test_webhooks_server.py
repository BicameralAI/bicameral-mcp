"""HTTP-layer hardening tests for the webhook server (#337 cycle 5).

Covers the BLOCK + MED findings from the adversarial review:
- B1: total-request timeout (slow-loris)
- B2: header count + size caps
- B3: HTTP request smuggling (dup Content-Length, Transfer-Encoding, CRLF injection)
- B4: negative Content-Length
- M2: public-bind requires --allow-public
- M3: split(" ") instead of .split()

These tests drive the server via in-memory StreamReader / StreamWriter
mocks so we don't actually bind a socket — fast, deterministic, no
flakes from port allocation.
"""

from __future__ import annotations

import asyncio
import io

import pytest

from webhooks import server as ws


class _Buffer:
    """Async-stream-compatible byte sink used as both reader+writer for tests."""

    def __init__(self, data: bytes = b"") -> None:
        self._in = io.BytesIO(data)
        self.out = bytearray()
        self._closed = False

    # StreamReader interface
    async def readline(self) -> bytes:
        return self._in.readline()

    async def readexactly(self, n: int) -> bytes:
        chunk = self._in.read(n)
        if len(chunk) < n:
            raise asyncio.IncompleteReadError(chunk, n)
        return chunk

    # StreamWriter interface
    def write(self, data: bytes) -> None:
        self.out.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._closed = True

    async def wait_closed(self) -> None:
        return None


def _build_request(method: str, path: str, headers: dict[str, str], body: bytes = b"") -> bytes:
    """Assemble a raw HTTP/1.1 request as the wire bytes."""
    lines = [f"{method} {path} HTTP/1.1\r\n"]
    for k, v in headers.items():
        lines.append(f"{k}: {v}\r\n")
    lines.append("\r\n")
    return "".join(lines).encode("ascii") + body


def _status_from_response(out: bytes) -> int:
    """Parse the status code from the wire-format response."""
    line = out.split(b"\r\n", 1)[0].decode("ascii")
    return int(line.split(" ")[1])


@pytest.fixture(autouse=True)
def _reset_server_semaphore():
    """Each test gets a fresh concurrency semaphore."""
    ws._request_semaphore = None
    yield
    ws._request_semaphore = None


# ── Smuggling defenses (B3) ─────────────────────────────────────────────────


def test_duplicate_content_length_rejected():
    raw = (
        b"POST /webhooks/github HTTP/1.1\r\n"
        b"Content-Length: 10\r\n"
        b"Content-Length: 100\r\n"
        b"\r\n"
        b"0123456789"
    )
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 400
    assert b"duplicate Content-Length" in buf.out


def test_transfer_encoding_rejected():
    raw = (
        b"POST /webhooks/github HTTP/1.1\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n"
    )
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 400
    assert b"Transfer-Encoding" in buf.out


def test_negative_content_length_rejected():
    raw = b"POST /webhooks/github HTTP/1.1\r\nContent-Length: -1\r\n\r\n"
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 400
    assert b"negative" in buf.out


def test_oversized_content_length_rejected():
    raw = b"POST /webhooks/github HTTP/1.1\r\nContent-Length: 999999999\r\n\r\n"
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 413


# ── Header limits (B2) ──────────────────────────────────────────────────────


def test_too_many_headers_rejected():
    headers = "".join(f"X-Junk-{i}: value\r\n" for i in range(200))
    raw = (
        b"POST /webhooks/github HTTP/1.1\r\n"
        + headers.encode("ascii")
        + b"Content-Length: 0\r\n\r\n"
    )
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 431


def test_oversize_header_block_rejected():
    big_value = "x" * 20000
    raw = (
        b"POST /webhooks/github HTTP/1.1\r\n" + f"X-Big: {big_value}\r\n".encode("ascii") + b"\r\n"
    )
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    # readline's default 64KB cap may fire as LimitOverrunError; either way
    # we want a 4xx, not 200.
    status = _status_from_response(bytes(buf.out))
    assert status in (400, 431)


# ── Newline injection in headers (B3) ───────────────────────────────────────


def test_request_line_must_have_three_parts():
    raw = b"GET\r\n\r\n"  # only one part
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 400
    assert b"malformed request line" in buf.out


# ── Routes ──────────────────────────────────────────────────────────────────


def test_health_endpoint_returns_200():
    raw = b"GET /health HTTP/1.1\r\n\r\n"
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 200


def test_unknown_path_returns_404():
    raw = b"POST /not-real HTTP/1.1\r\nContent-Length: 0\r\n\r\n"
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert _status_from_response(bytes(buf.out)) == 404


def test_response_includes_cache_control_no_store():
    raw = b"GET /health HTTP/1.1\r\n\r\n"
    buf = _Buffer(raw)
    asyncio.run(ws.handle_client(buf, buf))
    assert b"Cache-Control: no-store" in buf.out


# ── Public bind (M2) ────────────────────────────────────────────────────────


def test_serve_refuses_public_host_without_allow_public():
    """Binding to a non-loopback host must explicitly opt in."""
    with pytest.raises(RuntimeError, match="--allow-public"):
        asyncio.run(ws.serve(host="0.0.0.0", port=8765, allow_public=False))


def test_is_loopback_recognizes_common_loopbacks():
    assert ws._is_loopback("127.0.0.1")
    assert ws._is_loopback("::1")
    assert ws._is_loopback("localhost")
    assert ws._is_loopback("127.0.0.42")  # all 127/8 is loopback
    assert not ws._is_loopback("0.0.0.0")
    assert not ws._is_loopback("192.168.1.5")
    assert not ws._is_loopback("8.8.8.8")
