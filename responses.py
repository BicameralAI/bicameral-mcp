"""ToolResponse formatting for MCP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from mcp.types import TextContent

PREFLIGHT_STAGES = ("capture", "projection", "lookup", "enforcement")


def format_tool_response(response: dict[str, Any]) -> TextContent:
    return TextContent(type="text", text=json.dumps(response, indent=2, sort_keys=True))


def format_preflight_response(response: dict[str, Any]) -> TextContent:
    """Render a preflight daemon response with explicit staged section.

    Extracts the ``staged`` key added by bot#323 and surfaces each stage
    status at the top level of the MCP output.  Stages missing from the
    daemon payload are rendered as ``unsupported``.  ``enforcement.status``
    of ``not_configured`` is never promoted to warn/pause/block behavior.
    ``session_directive`` is forwarded as-is from the daemon.
    """
    staged: dict[str, Any] = response.get("staged", {})
    stages: dict[str, Any] = {}

    for stage_name in PREFLIGHT_STAGES:
        stage_data = staged.get(stage_name)
        if stage_data is None:
            stages[stage_name] = {"status": "unsupported"}
        else:
            stages[stage_name] = stage_data

    enforcement = stages.get("enforcement", {})
    if enforcement.get("status") == "not_configured":
        enforcement["behavior"] = "none"

    session_directive = staged.get("session_directive", {"mode": "continue"})

    mcp_output: dict[str, Any] = {
        "status": response.get("status", "ok"),
        "request_id": response.get("request_id"),
        "stages": stages,
        "session_directive": session_directive,
        "result": {key: value for key, value in response.items() if key != "staged"},
    }
    return TextContent(type="text", text=json.dumps(mcp_output, indent=2, sort_keys=True))


def error_text(code: str, message: str) -> TextContent:
    payload = {
        "status": "error",
        "message": message,
        "error_code": code,
        "responded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    return TextContent(type="text", text=json.dumps(payload, indent=2, sort_keys=True))
