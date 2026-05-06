"""Functionality tests for the MCP-boundary translation of
``_IngestRefused`` (#216 Phase 1 + Phase 2).

The handler raises ``_IngestRefused``; ``server.call_tool`` translates
to a ``TextContent`` carrying ``error`` / ``detail`` / ``action``
fields. Schema (``IngestResponse``) is unchanged — refusals never
return a populated IngestResponse.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import TextContent

import server
from handlers.ingest import _IngestRefused


def _stub_ctx() -> MagicMock:
    """Minimal ctx; the server-boundary tests never touch its inner state."""
    ctx = MagicMock()
    ctx.repo_path = "."
    return ctx


@pytest.mark.asyncio
async def test_call_tool_translates_size_limit_refusal_to_text_content_error() -> None:
    raised = _IngestRefused("size_limit_exceeded", detail="2048 bytes > 1024 cap")
    sync_stub = AsyncMock(return_value=None)
    handle_stub = AsyncMock(side_effect=raised)
    ctx_stub = _stub_ctx()

    with (
        patch.object(server.BicameralContext, "from_env", return_value=ctx_stub),
        patch("handlers.sync_middleware.ensure_ledger_synced", sync_stub),
        patch.object(server, "handle_ingest", handle_stub),
    ):
        result = await server.call_tool("bicameral.ingest", {"payload": {"k": "v"}})

    assert isinstance(result, list)
    assert len(result) == 1
    entry = result[0]
    assert isinstance(entry, TextContent)
    body = json.loads(entry.text)
    assert body["error"] == "size_limit_exceeded"
    assert body["detail"] == "2048 bytes > 1024 cap"
    assert isinstance(body["action"], str) and body["action"]


@pytest.mark.asyncio
async def test_call_tool_action_string_for_size_limit_directs_operator_to_config_knob() -> None:
    raised = _IngestRefused("size_limit_exceeded", detail="x bytes > y cap")
    sync_stub = AsyncMock(return_value=None)
    handle_stub = AsyncMock(side_effect=raised)
    ctx_stub = _stub_ctx()

    with (
        patch.object(server.BicameralContext, "from_env", return_value=ctx_stub),
        patch("handlers.sync_middleware.ensure_ledger_synced", sync_stub),
        patch.object(server, "handle_ingest", handle_stub),
    ):
        result = await server.call_tool("bicameral.ingest", {"payload": {"k": "v"}})

    body = json.loads(result[0].text)
    action = body["action"]
    # Operator-actionable guidance: must mention BOTH the config remedy
    # (raise the cap) and the operator-side remedy (split the payload).
    assert "ingest_max_bytes" in action
    assert "split the payload" in action
