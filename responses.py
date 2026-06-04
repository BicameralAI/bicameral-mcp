"""ToolResponse formatting for MCP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp.types import TextContent


def format_tool_response(response: dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(response, indent=2, sort_keys=True))


def error_text(code: str, message: str) -> TextContent:
    payload = {
        "status": "error",
        "message": message,
        "error_code": code,
        "responded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    return TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))
