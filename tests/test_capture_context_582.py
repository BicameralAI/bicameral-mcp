"""MCP context capture as bot-owned Source/EvidenceReference ingest input."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import server
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS
from tool_schemas import tool_for_name
from version import TOOLREQUEST_PROTOCOL_VERSION


class _CaptureDaemon:
    def __init__(self, *, commands: list[str] | None = None) -> None:
        self.commands = commands if commands is not None else list(SUPPORTED_COMMANDS)
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": self.commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"candidate_id": "cand-582"},
        }


def test_capture_context_schema_exposes_source_evidence_and_code_hint_fields():
    tool = tool_for_name("bicameral.capture_context")

    assert tool is not None
    props = tool.inputSchema["properties"]
    for key in (
        "source_uri",
        "source_type",
        "source_kind",
        "source_link",
        "session_turns",
        "tool_calls",
        "tool_outputs",
        "command_outputs",
        "code_hints",
        "code_region_hints",
        "evidence_references",
        "correlation_id",
    ):
        assert key in props


@pytest.mark.asyncio
async def test_capture_context_maps_to_ingest_submit_local(monkeypatch):
    daemon = _CaptureDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)

    await server.call_tool(
        "bicameral.capture_context",
        {
            "session_id": "sess-582",
            "correlation_id": "corr-582",
            "source_link": "mcp://session/sess-582",
            "session_turns": [{"role": "assistant", "content": "Need to update retry docs."}],
            "tool_calls": [{"tool": "apply_patch", "request_id": "tool-1"}],
            "tool_outputs": [{"tool": "pytest", "output": "2 passed"}],
            "command_outputs": [{"command": "pytest -q", "stdout": "2 passed"}],
            "code_hints": [
                {"path": "src/billing/retry.py", "range": "10:1-30:1", "symbol": "Retry"}
            ],
            "code_region_hints": [
                {"path": "src/billing/retry.py", "start_line": 10, "end_line": 30}
            ],
            "evidence_references": [{"kind": "command_output", "id": "cmd-582"}],
            "metadata": {"caller": "codex"},
        },
    )

    request = daemon.requests[0]
    params = request["command"]["params"]
    metadata = params["metadata"]
    snapshot = json.loads(params["snapshot_content"])

    assert MCP_TOOL_COMMANDS["bicameral.capture_context"] == "ingest.submit_local"
    assert request["command"]["name"] == "ingest.submit_local"
    assert params["source_uri"] == "mcp://session/corr-582"
    assert params["source_type"] == "mcp_session"
    assert params["title"] == "MCP context capture"
    assert params["binding_hints"] == [
        {"file": "src/billing/retry.py", "range": "10:1-30:1", "symbol": "Retry"}
    ]
    assert {"excerpt": "2 passed"} in params["evidence"]
    assert {"excerpt": "Need to update retry docs."} in params["evidence"]
    assert snapshot["kind"] == "SourceSnapshot"
    assert snapshot["source"]["kind"] == "Source"
    assert snapshot["evidence_references"] == [{"kind": "command_output", "id": "cmd-582"}]
    assert metadata["bot_vocabulary"] == [
        "Source",
        "SourceSnapshot",
        "EvidenceReference",
        "SourceKind",
    ]
    assert metadata["correlation_id"] == "corr-582"
    assert metadata["mcp_session_id"] == "sess-582"
    assert metadata["mcp_capture"]["tool"] == "bicameral.capture_context"
    assert metadata["evidence_references"] == [{"kind": "command_output", "id": "cmd-582"}]
    assert metadata["code_region_hints"] == [
        {"path": "src/billing/retry.py", "start_line": 10, "end_line": 30}
    ]


@pytest.mark.asyncio
async def test_capture_context_does_not_forward_control_keys_as_ingest_params(monkeypatch):
    daemon = _CaptureDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)

    await server.call_tool(
        "bicameral.capture_context",
        {
            "actor_id": "agent-582",
            "session_id": "sess-582",
            "workspace": "/repo",
            "policy_scope": ["default"],
            "command_outputs": [{"stdout": "ok"}],
        },
    )

    request = daemon.requests[0]
    params = request["command"]["params"]

    assert "actor_id" not in params
    assert "session_id" not in params
    assert "workspace" not in params
    assert "policy_scope" not in params
    assert request["authority"]["actor_id"] == "agent-582"
    assert request["authority"]["session_id"] == "sess-582"
    assert params["metadata"]["mcp_session_id"] == "sess-582"


@pytest.mark.asyncio
async def test_capture_context_is_capability_gated_on_ingest_submit_local(monkeypatch):
    daemon = _CaptureDaemon(
        commands=[command for command in SUPPORTED_COMMANDS if command != "ingest.submit_local"]
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool(
        "bicameral.capture_context",
        {"command_outputs": [{"stdout": "ok"}]},
    )
    rendered = json.loads(content[0].text)

    assert rendered["status"] == "error"
    assert rendered["error_code"] == "daemon_capability_error"
    assert rendered["recovery"]["requested_tool"] == "bicameral.capture_context"
    assert rendered["recovery"]["requested_command"] == "ingest.submit_local"
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_capture_context_creates_no_local_source_snapshot_files(monkeypatch, tmp_path):
    daemon = _CaptureDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.chdir(tmp_path)

    before = _snapshot_local_files(tmp_path)
    await server.call_tool(
        "bicameral.capture_context",
        {"code_hints": [{"file": "src/main.py"}], "command_outputs": [{"stdout": "ok"}]},
    )
    after = _snapshot_local_files(tmp_path)

    assert before == after


def _snapshot_local_files(workspace: Path) -> set[Path]:
    return {path for path in workspace.rglob("*") if path.is_file()}
