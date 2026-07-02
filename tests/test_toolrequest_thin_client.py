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
from tool_request import (
    LOCAL_ONLY_TOOLS,
    MCP_TOOL_COMMANDS,
    VALID_DECISION_LEVELS,
    build_tool_request,
)
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

    assert names == set(MCP_TOOL_COMMANDS) | LOCAL_ONLY_TOOLS
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
    # Coverage guard (#343) dispatches a lookup.query before the primary preflight.run.
    preflight_req = next(r for r in fake.requests if r["command"]["name"] == "preflight.run")
    assert preflight_req["command"] == {
        "name": "preflight.run",
        "params": {
            "files": ["src/lib.rs"],
            "symbols": ["DecisionLedger"],
            "branch": "feature/x",
        },
    }
    assert preflight_req["authority"]["auth_method"] == "mcp_session"
    assert preflight_req["authority"]["actor_id"] == "agent-123"
    assert preflight_req["authority"]["workspace"] == "/repo"
    assert preflight_req["authority"]["audit_metadata"]["surface"] == "mcp"
    assert preflight_req["authority"]["audit_metadata"]["mcp_tool"] == "bicameral.preflight"


@pytest.mark.asyncio
async def test_preflight_includes_checkpoint_hint_when_provided(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-hint")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

    content = await server.call_tool(
        "bicameral.preflight",
        {
            "files": ["src/main.rs"],
            "checkpoint_hint": "pre_work",
        },
    )

    response = json.loads(content[0].text)
    assert response["status"] == "ok"
    # Coverage guard (#343) dispatches lookup.query first; find preflight.run.
    preflight_req = next(r for r in fake.requests if r["command"]["name"] == "preflight.run")
    assert preflight_req["command"] == {
        "name": "preflight.run",
        "params": {
            "files": ["src/main.rs"],
            "checkpoint_hint": "pre_work",
        },
    }


@pytest.mark.asyncio
async def test_preflight_omits_checkpoint_hint_when_absent(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-no-hint")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["a.py"], "symbols": ["Foo"]},
    )

    response = json.loads(content[0].text)
    assert response["status"] == "ok"
    # Coverage guard (#343) dispatches lookup.query first; find preflight.run.
    preflight_req = next(r for r in fake.requests if r["command"]["name"] == "preflight.run")
    params = preflight_req["command"]["params"]
    assert "checkpoint_hint" not in params
    assert params == {"files": ["a.py"], "symbols": ["Foo"]}


@pytest.mark.asyncio
async def test_checkpoint_hint_does_not_change_authority(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-auth")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

    await server.call_tool(
        "bicameral.preflight",
        {"files": ["x.py"], "checkpoint_hint": "pre_write"},
    )
    await server.call_tool(
        "bicameral.preflight",
        {"files": ["x.py"]},
    )

    # Coverage guard (#343) dispatches lookup.query before each preflight.run.
    preflight_reqs = [r for r in fake.requests if r["command"]["name"] == "preflight.run"]
    auth_with_hint = preflight_reqs[0]["authority"]
    auth_without_hint = preflight_reqs[1]["authority"]
    assert auth_with_hint["actor_id"] == auth_without_hint["actor_id"]
    assert auth_with_hint["auth_method"] == auth_without_hint["auth_method"]
    assert auth_with_hint["workspace"] == auth_without_hint["workspace"]
    assert auth_with_hint["policy_scope"] == auth_without_hint["policy_scope"]
    assert (
        auth_with_hint["audit_metadata"]["mcp_tool"]
        == auth_without_hint["audit_metadata"]["mcp_tool"]
    )


def test_preflight_toolrequest_shape_with_checkpoint_hint():
    request = build_tool_request(
        command_name="preflight.run",
        params={
            "files": ["src/lib.rs"],
            "symbols": ["LedgerStore"],
            "checkpoint_hint": "mid_session",
            "actor_id": "should-be-stripped",
        },
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )

    assert request["command"] == {
        "name": "preflight.run",
        "params": {
            "files": ["src/lib.rs"],
            "symbols": ["LedgerStore"],
            "checkpoint_hint": "mid_session",
        },
    }
    assert request["authority"]["auth_method"] == "mcp_session"
    assert request["issued_at"].endswith("Z")


def test_preflight_schema_exposes_checkpoint_hint():
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.preflight")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "checkpoint_hint" in props
    assert props["checkpoint_hint"]["type"] == "string"


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

    assert {"preflight", "bind", "ingest", "history_search", "evidence_refresh", "brief"} <= names
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


@pytest.mark.asyncio
async def test_evidence_refresh_maps_to_thin_toolrequest(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.evidence.refresh",
        {
            "decision_id": "DEC-7",
            "workspace": "/repo",
            "actor_id": "agent-refresh",
            "link_commit": "must-not-forward",
            "ensure_ledger_synced": True,
        },
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert fake.requests[0]["command"] == {
        "name": "evidence.refresh",
        "params": {"decision_id": "DEC-7"},
    }
    assert fake.requests[0]["authority"]["actor_id"] == "agent-refresh"
    assert fake.requests[0]["authority"]["workspace"] == "/repo"


@pytest.mark.asyncio
async def test_evidence_refresh_absent_capability_returns_typed_error(monkeypatch):
    class _NoEvidenceRefreshClient(_FakeClient):
        async def capabilities(self) -> dict:
            capabilities = await super().capabilities()
            capabilities["supported_commands"] = [
                command
                for command in capabilities["supported_commands"]
                if command != "evidence.refresh"
            ]
            return capabilities

    fake = _NoEvidenceRefreshClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.evidence.refresh",
        {"decision_id": "DEC-7"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "daemon_capability_error"
    assert response["recovery"]["requested_tool"] == "bicameral.evidence.refresh"
    assert response["recovery"]["requested_command"] == "evidence.refresh"
    assert fake.requests == []


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


# ---------------------------------------------------------------------------
# decision_level classification on ingest (#340)
# ---------------------------------------------------------------------------


def test_valid_decision_levels_is_l1_l2_l3():
    assert VALID_DECISION_LEVELS == {"L1", "L2", "L3"}


def test_ingest_toolrequest_with_decision_level():
    request = build_tool_request(
        command_name="ingest.submit_local",
        params={
            "source_uri": "local://notes.md",
            "source_type": "session",
            "title": "Unit test",
            "description": "decision_level forwarding",
            "decision_level": "L2",
            "actor_id": "control-stripped",
        },
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )
    params = request["command"]["params"]
    assert params["decision_level"] == "L2"
    assert "pending_classification" not in params
    assert "actor_id" not in params


def test_ingest_toolrequest_without_decision_level_gets_pending():
    request = build_tool_request(
        command_name="ingest.submit_local",
        params={
            "source_uri": "local://notes.md",
            "source_type": "session",
            "title": "Unit test",
            "description": "no decision_level",
        },
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )
    params = request["command"]["params"]
    assert "decision_level" not in params
    assert params["pending_classification"] is True


def test_ingest_schema_exposes_decision_level():
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.ingest")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "decision_level" in props
    assert props["decision_level"]["type"] == "string"
    assert props["decision_level"]["enum"] == ["L1", "L2", "L3"]


# ---------------------------------------------------------------------------
# recall.inspect_evidence & recall.expand_scope (#678)
# ---------------------------------------------------------------------------


def test_recall_inspect_evidence_schema_exists():
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.recall.inspect_evidence")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "packet_id" in props
    assert "match_id" in props
    assert "evidence_id" in props
    assert tool.inputSchema["required"] == ["packet_id", "match_id"]


def test_recall_inspect_evidence_description_honesty_language():
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.recall.inspect_evidence")
    assert tool is not None
    desc = tool.description
    assert "searched scope" in desc.lower() or "unknown scope" in desc.lower()
    for forbidden in ("compliance", "signoff", "enforcement"):
        assert forbidden not in desc.lower() or "not create, mutate, or verify" in desc.lower()


def test_recall_expand_scope_schema_exists():
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.recall.expand_scope")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "packet_id" in props
    assert "expand_to" in props
    assert "reason" in props
    assert tool.inputSchema["required"] == ["packet_id"]


def test_recall_expand_scope_description_honesty_language():
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.recall.expand_scope")
    assert tool is not None
    desc = tool.description
    assert "unknown scope" in desc.lower() or "searched" in desc.lower()
    for forbidden in ("compliance", "signoff", "enforcement"):
        assert forbidden not in desc.lower() or "not create, mutate, or verify" in desc.lower()


def test_recall_inspect_evidence_toolrequest_shape():
    request = build_tool_request(
        command_name="recall.inspect_evidence",
        params={
            "packet_id": "pkt-001",
            "match_id": "match-A",
            "evidence_id": "ev-1",
            "actor_id": "should-be-stripped",
        },
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )
    assert request["command"] == {
        "name": "recall.inspect_evidence",
        "params": {
            "packet_id": "pkt-001",
            "match_id": "match-A",
            "evidence_id": "ev-1",
        },
    }
    assert "actor_id" not in request["command"]["params"]
    assert request["authority"]["auth_method"] == "mcp_session"


def test_recall_expand_scope_toolrequest_shape():
    request = build_tool_request(
        command_name="recall.expand_scope",
        params={
            "packet_id": "pkt-002",
            "expand_to": ["source-B", "source-C"],
            "reason": "wider coverage needed",
            "actor_id": "should-be-stripped",
        },
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )
    assert request["command"] == {
        "name": "recall.expand_scope",
        "params": {
            "packet_id": "pkt-002",
            "expand_to": ["source-B", "source-C"],
            "reason": "wider coverage needed",
        },
    }
    assert "actor_id" not in request["command"]["params"]


def test_recall_expand_scope_toolrequest_minimal():
    request = build_tool_request(
        command_name="recall.expand_scope",
        params={"packet_id": "pkt-003"},
        authority={"actor_id": "u", "auth_method": "mcp_session"},
    )
    assert request["command"]["params"] == {"packet_id": "pkt-003"}


@pytest.mark.asyncio
async def test_recall_inspect_evidence_maps_to_daemon(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.recall.inspect_evidence",
        {"packet_id": "pkt-100", "match_id": "match-X"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert response["scope_note"]
    req = fake.requests[-1]
    assert req["command"]["name"] == "recall.inspect_evidence"
    assert req["command"]["params"] == {"packet_id": "pkt-100", "match_id": "match-X"}


@pytest.mark.asyncio
async def test_recall_expand_scope_maps_to_daemon(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.recall.expand_scope",
        {"packet_id": "pkt-200", "expand_to": ["extra-source"]},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert response["scope_note"]
    req = fake.requests[-1]
    assert req["command"]["name"] == "recall.expand_scope"
    assert req["command"]["params"] == {
        "packet_id": "pkt-200",
        "expand_to": ["extra-source"],
    }


@pytest.mark.asyncio
async def test_recall_inspect_evidence_absent_capability_returns_error(monkeypatch):
    """When daemon does not advertise recall.inspect_evidence, MCP returns a typed error."""

    class _NoRecallInspectClient(_FakeClient):
        async def capabilities(self) -> dict:
            caps = await super().capabilities()
            caps["supported_commands"] = [
                c for c in caps["supported_commands"] if c != "recall.inspect_evidence"
            ]
            return caps

    fake = _NoRecallInspectClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.recall.inspect_evidence",
        {"packet_id": "pkt-absent", "match_id": "m1"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "daemon_capability_error"
    assert response["recovery"]["requested_tool"] == "bicameral.recall.inspect_evidence"
    assert response["recovery"]["requested_command"] == "recall.inspect_evidence"
    assert fake.requests == []


@pytest.mark.asyncio
async def test_recall_expand_scope_absent_capability_returns_error(monkeypatch):
    """When daemon does not advertise recall.expand_scope, MCP returns a typed error."""

    class _NoRecallExpandClient(_FakeClient):
        async def capabilities(self) -> dict:
            caps = await super().capabilities()
            caps["supported_commands"] = [
                c for c in caps["supported_commands"] if c != "recall.expand_scope"
            ]
            return caps

    fake = _NoRecallExpandClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool(
        "bicameral.recall.expand_scope",
        {"packet_id": "pkt-absent"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "daemon_capability_error"
    assert response["recovery"]["requested_tool"] == "bicameral.recall.expand_scope"
    assert response["recovery"]["requested_command"] == "recall.expand_scope"
    assert fake.requests == []


def test_recall_command_mapping_completeness():
    """Verify recall.inspect_evidence and recall.expand_scope are mapped."""
    assert MCP_TOOL_COMMANDS["bicameral.recall.inspect_evidence"] == "recall.inspect_evidence"
    assert MCP_TOOL_COMMANDS["bicameral.recall.expand_scope"] == "recall.expand_scope"


def test_recall_request_correction_not_in_new_recall_namespace():
    """recall.request_correction is NOT exposed in bicameral.recall.* namespace (bot#663 open)."""
    recall_tools = [name for name in MCP_TOOL_COMMANDS if name.startswith("bicameral.recall.")]
    assert "bicameral.recall.request_correction" not in recall_tools
    assert set(recall_tools) == {
        "bicameral.recall.inspect_evidence",
        "bicameral.recall.expand_scope",
    }


# ---------------------------------------------------------------------------
# Deferred capability handling (#677)
# ---------------------------------------------------------------------------


class _DeferredCapabilityClient(_FakeClient):
    """Fake daemon that reports review.resolve_compliance as deferred."""

    async def capabilities(self) -> dict:
        caps = await super().capabilities()
        caps["supported_commands"] = [
            cmd for cmd in caps["supported_commands"] if cmd != "review.resolve_compliance"
        ]
        caps["deferred_commands"] = ["review.resolve_compliance"]
        return caps


class _AbsentCapabilityClient(_FakeClient):
    """Fake daemon that does not advertise review.resolve_compliance at all."""

    async def capabilities(self) -> dict:
        caps = await super().capabilities()
        caps["supported_commands"] = [
            cmd for cmd in caps["supported_commands"] if cmd != "review.resolve_compliance"
        ]
        return caps


@pytest.mark.asyncio
async def test_deferred_command_hidden_from_list_tools(monkeypatch):
    """When daemon reports a command as deferred, its MCP tool is hidden."""
    monkeypatch.setattr(server, "_client", lambda: _DeferredCapabilityClient())

    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert "bicameral.review.resolve_compliance" not in names
    assert "bicameral.preflight" in names
    assert "bicameral.history" in names


@pytest.mark.asyncio
async def test_deferred_command_call_returns_typed_error(monkeypatch):
    """Calling a deferred command returns a typed daemon_capability_error with deferred flag."""
    monkeypatch.setattr(server, "_client", lambda: _DeferredCapabilityClient())

    content = await server.call_tool(
        "bicameral.review.resolve_compliance",
        {"target_id": "DEC-1", "compliance_verdict": "reflected"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "daemon_capability_error"
    assert response["recovery"]["deferred"] is True
    assert response["recovery"]["requested_tool"] == "bicameral.review.resolve_compliance"
    assert response["recovery"]["requested_command"] == "review.resolve_compliance"


@pytest.mark.asyncio
async def test_absent_command_hidden_from_list_tools(monkeypatch):
    """When daemon does not advertise a command at all, its MCP tool is hidden."""
    monkeypatch.setattr(server, "_client", lambda: _AbsentCapabilityClient())

    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert "bicameral.review.resolve_compliance" not in names
    assert "bicameral.preflight" in names


@pytest.mark.asyncio
async def test_absent_command_call_returns_capability_error(monkeypatch):
    """Calling an absent command returns daemon_capability_error without deferred flag."""
    monkeypatch.setattr(server, "_client", lambda: _AbsentCapabilityClient())

    content = await server.call_tool(
        "bicameral.review.resolve_compliance",
        {"target_id": "DEC-1", "compliance_verdict": "reflected"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "error"
    assert response["error_code"] == "daemon_capability_error"
    assert "deferred" not in response["recovery"]
    assert response["recovery"]["requested_tool"] == "bicameral.review.resolve_compliance"


@pytest.mark.asyncio
async def test_supported_command_visible_and_callable(monkeypatch):
    """When daemon reports a command as supported, its MCP tool works normally."""
    fake = _FakeClient()
    monkeypatch.setattr(server, "_client", lambda: fake)

    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    assert "bicameral.review.resolve_compliance" in names

    content = await server.call_tool(
        "bicameral.review.resolve_compliance",
        {"target_id": "DEC-1", "compliance_verdict": "reflected"},
    )
    response = json.loads(content[0].text)
    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_local_only_tools_always_visible(monkeypatch):
    """Local-only tools remain visible regardless of daemon capability report."""
    monkeypatch.setattr(server, "_client", lambda: _DeferredCapabilityClient())

    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    for local_tool in LOCAL_ONLY_TOOLS:
        assert local_tool in names


def test_resolve_compliance_description_does_not_imply_alpha_support():
    """Tool description for review.resolve_compliance must not imply alpha support."""
    from tool_schemas import tool_for_name

    tool = tool_for_name("bicameral.review.resolve_compliance")
    assert tool is not None
    desc = tool.description.lower()
    assert "deferred" in desc
    assert "alpha" in desc
