from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import server
from tool_request import MCP_TOOL_COMMANDS, build_tool_request
from version import TOOLREQUEST_PROTOCOL_VERSION


class _FakeClient:
    def __init__(self, *, protocol_version: str = TOOLREQUEST_PROTOCOL_VERSION):
        self.protocol_version = protocol_version
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": tool_request["command"]["name"]},
            "responded_at": "2026-06-04T00:00:00Z",
        }


@pytest.mark.asyncio
async def test_list_tools_exposes_only_toolrequest_backed_surface(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: _FakeClient())

    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert names == set(MCP_TOOL_COMMANDS)
    assert "bicameral.link_commit" not in names
    assert "bicameral.ratify" not in names
    assert "validate_symbols" not in names
    assert "get_neighbors" not in names


@pytest.mark.asyncio
async def test_call_tool_maps_to_canonical_toolrequest(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-123")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["src/lib.rs"], "symbols": ["DecisionLedger"], "branch": "feature/x"},
    )

    response = json.loads(content[0].text)
    assert response["status"] == "ok"
    assert fake.requests[0]["command"] == {
        "name": "preflight.run",
        "params": {
            "files": ["src/lib.rs"],
            "symbols": ["DecisionLedger"],
            "branch": "feature/x",
        },
    }
    assert fake.requests[0]["authority"]["auth_method"] == "mcp_session"
    assert fake.requests[0]["authority"]["actor_id"] == "agent-123"
    assert fake.requests[0]["authority"]["workspace"] == "/repo"
    assert fake.requests[0]["authority"]["audit_metadata"]["surface"] == "mcp"
    assert fake.requests[0]["authority"]["audit_metadata"]["mcp_tool"] == "bicameral.preflight"


@pytest.mark.asyncio
async def test_protocol_mismatch_fails_before_dispatch(monkeypatch):
    fake = _FakeClient(protocol_version="v1")
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.history", {})

    response = json.loads(content[0].text)
    assert response["status"] == "error"
    assert response["error_code"] == "daemon_protocol_mismatch"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_prompts_are_generic_tool_workflows(monkeypatch):
    monkeypatch.setattr(server, "_client", lambda: _FakeClient())

    prompts = await server.list_prompts()
    names = {prompt.name for prompt in prompts}

    assert {"preflight", "bind", "ingest", "history_search"} <= names
    prompt = await server.get_prompt("preflight", {"branch": "feature/x"})
    text = prompt.messages[0].content.text
    assert "bicameral.preflight" in text
    assert "branch=feature/x" in text


def test_tool_request_shape_matches_v2_contract():
    request = build_tool_request(
        command_name="review.resolve_compliance",
        params={
            "target_id": "DEC-1",
            "compliance_verdict": "reflected",
            "reason": "verified",
            "actor_id": "ignored-control-key",
        },
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )

    assert request["command"] == {
        "name": "review.resolve_compliance",
        "params": {
            "target_id": "DEC-1",
            "compliance_verdict": "reflected",
            "reason": "verified",
        },
    }
    assert request["authority"]["auth_method"] == "mcp_session"
    assert request["issued_at"].endswith("Z")


def test_server_imports_no_legacy_authority_modules():
    tree = ast.parse(Path("server.py").read_text())
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])

    forbidden = {
        "adapters",
        "code_locator",
        "codegenome",
        "daemon",
        "dashboard",
        "events",
        "governance",
        "handlers",
        "integrations",
        "ledger",
        "sources",
    }
    assert imported_roots.isdisjoint(forbidden)


def test_packaging_includes_only_thin_client_modules():
    text = Path("pyproject.toml").read_text()
    assert "[tool.hatch.build.targets.wheel]" in text
    for module in [
        "server.py",
        "authority.py",
        "daemon_client.py",
        "prompts.py",
        "responses.py",
        "tool_request.py",
        "tool_schemas.py",
        "version.py",
    ]:
        assert f'  "{module}",' in text

    for deleted_path in [
        "code_locator",
        "codegenome",
        "daemon",
        "dashboard",
        "events",
        "governance",
        "handlers",
        "integrations",
        "ledger",
        "sources",
        "skills",
    ]:
        assert not Path(deleted_path).exists()
