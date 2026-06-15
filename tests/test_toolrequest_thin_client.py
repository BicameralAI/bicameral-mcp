from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import server
from daemon_client import (
    DEFAULT_DAEMON_URL,
    DaemonCapabilityError,
    DaemonConnectionError,
)
from responses import build_recovery_payload, format_preflight_response
from tool_request import MCP_TOOL_COMMANDS, build_tool_request
from version import TOOLREQUEST_PROTOCOL_VERSION

# Alpha staged preflight response fixture from bot#323.
STAGED_PREFLIGHT_FIXTURE: dict = {
    "capture": {"status": "not_configured"},
    "projection": {"status": "not_configured"},
    "lookup": {
        "status": "completed",
        "decision_refs": [
            "a0000001-0000-4000-8000-000000000001",
            "a0000001-0000-4000-8000-000000000002",
        ],
        "limitations": [
            "graph: Graph data unavailable. Using source-only preflight.",
            "team_active_prs: Local preflight cannot see team-wide active PR branches.",
        ],
    },
    "enforcement": {"status": "not_configured"},
    "session_directive": {"mode": "continue"},
}


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
        base = {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": tool_request["command"]["name"]},
            "responded_at": "2026-06-04T00:00:00Z",
        }
        if tool_request["command"]["name"] == "preflight.run":
            base["staged"] = STAGED_PREFLIGHT_FIXTURE
        return base


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
    assert "stages" in response
    assert "session_directive" in response
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


# ---------------------------------------------------------------------------
# Staged preflight response rendering (bot#323 / mcp#586)
# ---------------------------------------------------------------------------


def _daemon_response_with_staged(staged: dict | None = None) -> dict:
    """Build a minimal daemon preflight.run response with optional staged section."""
    response: dict = {
        "request_id": "req-staged-001",
        "status": "ok",
        "relevant_decisions": [],
        "relevant_candidates": [],
        "readiness": {"unknown_scope": []},
        "warnings": [],
        "responded_at": "2026-06-13T00:00:00Z",
    }
    if staged is not None:
        response["staged"] = staged
    return response


def test_staged_preflight_renders_all_stages():
    daemon_resp = _daemon_response_with_staged(STAGED_PREFLIGHT_FIXTURE)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    assert "stages" in output
    for stage in ("capture", "projection", "lookup", "enforcement"):
        assert stage in output["stages"]

    assert output["stages"]["capture"]["status"] == "not_configured"
    assert output["stages"]["projection"]["status"] == "not_configured"
    assert output["stages"]["lookup"]["status"] == "completed"
    assert output["stages"]["lookup"]["decision_refs"] == [
        "a0000001-0000-4000-8000-000000000001",
        "a0000001-0000-4000-8000-000000000002",
    ]
    assert len(output["stages"]["lookup"]["limitations"]) == 2
    assert output["stages"]["enforcement"]["status"] == "not_configured"


def test_staged_preflight_renders_session_directive():
    daemon_resp = _daemon_response_with_staged(STAGED_PREFLIGHT_FIXTURE)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    assert output["session_directive"] == {"mode": "continue"}


def test_enforcement_not_configured_never_becomes_blocking():
    daemon_resp = _daemon_response_with_staged(STAGED_PREFLIGHT_FIXTURE)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    enforcement = output["stages"]["enforcement"]
    assert enforcement["status"] == "not_configured"
    assert enforcement["behavior"] == "none"


def test_missing_staged_key_renders_unsupported():
    daemon_resp = _daemon_response_with_staged(staged=None)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    for stage in ("capture", "projection", "lookup", "enforcement"):
        assert output["stages"][stage]["status"] == "unsupported"
    assert output["session_directive"] == {"mode": "continue"}


def test_partial_staged_renders_present_and_unsupported():
    partial_staged = {
        "lookup": {
            "status": "completed",
            "decision_refs": ["dec-1"],
            "limitations": [],
        },
        "session_directive": {"mode": "continue"},
    }
    daemon_resp = _daemon_response_with_staged(partial_staged)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    assert output["stages"]["lookup"]["status"] == "completed"
    assert output["stages"]["capture"]["status"] == "unsupported"
    assert output["stages"]["projection"]["status"] == "unsupported"
    assert output["stages"]["enforcement"]["status"] == "unsupported"


def test_staged_result_preserves_daemon_payload():
    daemon_resp = _daemon_response_with_staged(STAGED_PREFLIGHT_FIXTURE)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    assert "result" in output
    assert output["result"]["status"] == "ok"
    assert "staged" not in output["result"]


def test_session_directive_warn_forwarded_as_is():
    staged_with_warn = {
        **STAGED_PREFLIGHT_FIXTURE,
        "session_directive": {"mode": "warn"},
    }
    daemon_resp = _daemon_response_with_staged(staged_with_warn)
    content = format_preflight_response(daemon_resp)
    output = json.loads(content.text)

    assert output["session_directive"] == {"mode": "warn"}


@pytest.mark.asyncio
async def test_preflight_uses_staged_formatter(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-staged")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

    content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
    response = json.loads(content[0].text)

    assert "stages" in response
    assert "session_directive" in response
    assert response["stages"]["lookup"]["status"] == "completed"
    assert response["stages"]["enforcement"]["behavior"] == "none"


@pytest.mark.asyncio
async def test_non_preflight_uses_generic_formatter(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.history", {})
    response = json.loads(content[0].text)

    assert "stages" not in response
    assert response["status"] == "ok"


# ---------------------------------------------------------------------------
# Daemon handshake recovery payloads (mcp#583)
# ---------------------------------------------------------------------------


class _RaisingClient:
    """Fake daemon client that can fail at handshake or dispatch."""

    def __init__(
        self,
        *,
        capabilities_error: Exception | None = None,
        send_error: Exception | None = None,
        protocol_version: str = TOOLREQUEST_PROTOCOL_VERSION,
    ):
        self.capabilities_error = capabilities_error
        self.send_error = send_error
        self.protocol_version = protocol_version
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        if self.capabilities_error is not None:
            raise self.capabilities_error
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        if self.send_error is not None:
            raise self.send_error
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {},
        }


def _clear_daemon_url_env(monkeypatch) -> None:
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


@pytest.mark.asyncio
async def test_daemon_unavailable_renders_recovery(monkeypatch):
    _clear_daemon_url_env(monkeypatch)
    fake = _RaisingClient(
        capabilities_error=DaemonConnectionError("cannot reach bicameral-bot daemon")
    )
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.history", {})
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "daemon_unavailable"
    recovery = response["recovery"]
    assert recovery["error_code"] == "daemon_unavailable"
    assert recovery["category"] == "setup"
    assert recovery["retryable"] is True
    assert recovery["requested_tool"] == "bicameral.history"
    assert recovery["requested_command"] == "history.list"
    assert "start" in recovery["operator_action"].lower()
    assert "daemon_url_override" not in recovery
    # Fail fast: no ToolRequest dispatched after handshake failure.
    assert fake.requests == []


@pytest.mark.asyncio
async def test_protocol_mismatch_recovery_includes_both_versions(monkeypatch):
    _clear_daemon_url_env(monkeypatch)
    fake = _RaisingClient(protocol_version="v1")
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.search", {"query": "x"})
    response = json.loads(content[0].text)

    assert response["error_code"] == "daemon_protocol_mismatch"
    recovery = response["recovery"]
    assert recovery["mcp_protocol_version"] == TOOLREQUEST_PROTOCOL_VERSION
    assert recovery["daemon_protocol_version"] == "v1"
    assert recovery["retryable"] is False
    assert fake.requests == []


@pytest.mark.asyncio
async def test_capability_error_recovery_includes_tool_and_command(monkeypatch):
    _clear_daemon_url_env(monkeypatch)
    fake = _RaisingClient(send_error=DaemonCapabilityError("daemon refused: unsupported_command"))
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.bind", {"decision_or_candidate_id": "DEC-1"})
    response = json.loads(content[0].text)

    assert response["error_code"] == "daemon_capability_error"
    recovery = response["recovery"]
    assert recovery["category"] == "capability"
    assert recovery["requested_tool"] == "bicameral.bind"
    assert recovery["requested_command"] == "binding.create"
    # The command was advertised-compatible enough to dispatch; daemon refused it.
    assert len(fake.requests) == 1


@pytest.mark.asyncio
async def test_wrong_daemon_url_calls_out_env_override(monkeypatch):
    monkeypatch.setenv("BICAMERAL_BOT_DAEMON_URL", "http://wrong-host:1234")
    fake = _RaisingClient(
        capabilities_error=DaemonConnectionError(
            "cannot reach bicameral-bot daemon",
            daemon_endpoint="http://wrong-host:1234",
        )
    )
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.history", {})
    response = json.loads(content[0].text)

    recovery = response["recovery"]
    assert recovery["daemon_url_override"]["env_var"] == "BICAMERAL_BOT_DAEMON_URL"
    assert recovery["daemon_url_override"]["value"] == "http://wrong-host:1234"
    assert "BICAMERAL_BOT_DAEMON_URL" in recovery["operator_action"]
    assert recovery["daemon_endpoint"] == "http://wrong-host:1234"


def test_build_recovery_payload_env_override_hint(monkeypatch):
    monkeypatch.setenv("BICAMERAL_DAEMON_URL", "http://example.invalid:9999")

    recovery = build_recovery_payload(
        error_code="daemon_unavailable",
        requested_tool="bicameral.history",
        requested_command="history.list",
    )

    assert recovery["daemon_url_override"]["env_var"] == "BICAMERAL_DAEMON_URL"
    assert recovery["daemon_url_override"]["value"] == "http://example.invalid:9999"
    assert "BICAMERAL_DAEMON_URL" in recovery["operator_action"]
    assert recovery["daemon_endpoint"] == "http://example.invalid:9999"


def test_build_recovery_payload_no_override_uses_default_endpoint(monkeypatch):
    _clear_daemon_url_env(monkeypatch)

    recovery = build_recovery_payload(error_code="daemon_unavailable")

    assert "daemon_url_override" not in recovery
    assert recovery["daemon_endpoint"] == DEFAULT_DAEMON_URL


def test_recovery_modules_import_no_legacy_authority():
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
    for module_file in ("responses.py", "daemon_client.py"):
        tree = ast.parse(Path(module_file).read_text())
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        assert imported_roots.isdisjoint(forbidden), module_file
