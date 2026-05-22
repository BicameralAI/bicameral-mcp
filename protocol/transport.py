"""Line-delimited JSON-RPC 2.0 framing over an asyncio stream.

Each message is a single JSON object terminated by ``\n``. Frames are
size-capped to defend against pathological payloads; oversized frames raise
``ProtocolError`` and the connection is closed by the caller.

This module does not own a socket — it only frames messages on a pair of
``asyncio.StreamReader`` / ``asyncio.StreamWriter`` provided by the caller.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from .contracts import ProtocolError

MAX_FRAME_BYTES = 16 * 1024 * 1024  # 16 MiB hard cap per message


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any]:
    """Read one JSON object terminated by newline.

    Raises ProtocolError on connection close mid-frame or on malformed JSON.
    """
    raw = await reader.readline()
    if not raw:
        raise ProtocolError("connection closed")
    if len(raw) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"malformed JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("frame is not a JSON object")
    return obj


async def write_message(
    writer: asyncio.StreamWriter, payload: dict[str, Any]
) -> None:
    """Write one JSON object framed with a trailing newline."""
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProtocolError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    writer.write(encoded + b"\n")
    await writer.drain()


def make_request(req_id: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}


def make_response(req_id: int | str | None, result: Any) -> dict[str, Any]:
    """Per JSON-RPC 2.0, the response id mirrors the request id; null if the
    request was a notification (server still writes a response here for
    diagnostics)."""
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id: int | None, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }
