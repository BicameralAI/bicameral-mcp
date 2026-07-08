"""MCP-assisted workspace bind flow tests (mcp#702, bicameral-bot#731).

Exercises the confirmed, cancelled, rejected, unsupported, already-bound (and
repair-required / unregistered) flows against a fake daemon whose ToolResponses
mirror the ``bicameral-bot`` ``workspace.bind`` contract fixtures.
"""

from __future__ import annotations

import json

import pytest

import server
from daemon_client import DaemonConnectionError
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION
from workspace_binding import build_binding_proposal, build_workspace_bind_command_args

WORKSPACE_BIND_COMMAND = "workspace.bind"

# Success result mirroring workspace-binding-bound.json (local.response).
BOUND_RESULT = {
    "project_id": "proj_ada",
    "state": "local_workspace_bound",
    "display": {
        "display_name": "Ada Service",
        "project_slug": "ada-service",
        "candidate_label": "ada-service",
    },
    "message": "Workspace binding materialized.",
}


def _error_result(kind: str, state: str, *, retry: bool) -> dict:
    """Build a WorkspaceBindErrorResponse-shaped result."""
    return {
        "error": kind,
        "project_id": "proj_ada",
        "message": f"typed {kind}",
        "state": state,
        "retry_after_repair": retry,
    }


class _FakeBindDaemon:
    def __init__(self, *, result: dict | None = None, supports_bind: bool = True):
        self.result = result
        self.supports_bind = supports_bind
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        commands = list(MCP_TOOL_COMMANDS.values())
        if not self.supports_bind:
            commands = [c for c in commands if c != WORKSPACE_BIND_COMMAND]
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": commands,
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": self.result if self.result is not None else {},
            "responded_at": "2026-07-08T00:00:00Z",
        }


def _patch(monkeypatch, daemon: _FakeBindDaemon) -> None:
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-bind")
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


def _base_args(**overrides) -> dict:
    args = {
        "project_id": "proj_ada",
        "display_name": "Ada Service",
        "candidate_path": "/home/alex/code/ada-service",
    }
    args.update(overrides)
    return args


# --- Proposal construction -------------------------------------------------


def test_proposal_uses_cwd_as_candidate_evidence_only():
    proposal = build_binding_proposal(_base_args())
    assert proposal["project_id"] == "proj_ada"
    assert proposal["candidate_path"] == "/home/alex/code/ada-service"
    assert proposal["source_surface"] == "mcp"
    # Identity is the registered project id, never the folder path.
    assert proposal["project_id"] != proposal["candidate_path"]
    assert proposal["display"]["candidate_label"] == "ada-service"


def test_proposal_requires_project_identity():
    with pytest.raises(ValueError):
        build_binding_proposal({"candidate_path": "/home/alex/code/ada-service"})


def test_command_args_default_required_capability_and_confirmed():
    args = build_workspace_bind_command_args(_base_args(confirmed=True))
    assert args["confirmed"] is True
    assert args["required_daemon_capability"] == 1
    assert args["proposal"]["candidate_path"] == "/home/alex/code/ada-service"


def test_missing_project_id_is_typed_error(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT)
    _patch(monkeypatch, daemon)

    content = _run(server.call_tool("bicameral.workspace.bind", {"confirmed": True}))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["error_code"] == "workspace_bind_invalid"
    assert daemon.requests == []


# --- Confirmation gate (never bind silently) -------------------------------


@pytest.mark.asyncio
async def test_unconfirmed_returns_prompt_without_dispatch(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT)
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args())
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "confirmation_required"
    assert parsed["prompt"] == ("Bind this folder to project Ada Service for local code grounding?")
    assert parsed["candidate_path"] == "/home/alex/code/ada-service"
    # Nothing is dispatched to the daemon before confirmation: never bind silently.
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_cancelled_flow_never_binds(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT)
    _patch(monkeypatch, daemon)

    # An operator who does not re-invoke with confirmed=true has cancelled: no
    # bind request ever reaches the daemon.
    await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=False))
    assert daemon.requests == []


# --- Confirmed dispatch and outcomes ---------------------------------------


@pytest.mark.asyncio
async def test_confirmed_binds(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT)
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=True))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "ok"
    assert parsed["outcome"] == "bound"
    assert parsed["state"] == "local_workspace_bound"

    assert len(daemon.requests) == 1
    request = daemon.requests[0]
    assert request["command"]["name"] == WORKSPACE_BIND_COMMAND
    params = request["command"]["params"]
    assert params["confirmed"] is True
    assert params["proposal"]["source_surface"] == "mcp"
    assert params["proposal"]["candidate_path"] == "/home/alex/code/ada-service"
    # Control keys never leak into command params.
    assert "actor_id" not in params
    assert request["authority"]["actor_id"] == "agent-bind"


@pytest.mark.asyncio
async def test_already_bound_flow(monkeypatch):
    daemon = _FakeBindDaemon(
        result=_error_result("already_bound", "local_workspace_bound", retry=False)
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=True))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["outcome"] == "already_bound"
    assert parsed["error_kind"] == "already_bound"
    assert parsed["state"] == "local_workspace_bound"


@pytest.mark.asyncio
async def test_rejected_unsafe_path_flow(monkeypatch):
    daemon = _FakeBindDaemon(
        result=_error_result("unsafe_path", "local_workspace_unbound", retry=True)
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.workspace.bind",
        _base_args(candidate_path="/etc", confirmed=True),
    )
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["outcome"] == "rejected"
    assert parsed["error_kind"] == "unsafe_path"
    assert "unsafe" in parsed["operator_action"].lower()


@pytest.mark.asyncio
async def test_repair_required_flow(monkeypatch):
    daemon = _FakeBindDaemon(
        result=_error_result("repair_required", "local_workspace_repair_required", retry=True)
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=True))
    parsed = json.loads(content[0].text)

    assert parsed["outcome"] == "repair_required"
    assert parsed["state"] == "local_workspace_repair_required"


@pytest.mark.asyncio
async def test_unregistered_project_flow(monkeypatch):
    daemon = _FakeBindDaemon(
        result=_error_result("unregistered_project", "local_workspace_unbound", retry=True)
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=True))
    parsed = json.loads(content[0].text)

    assert parsed["outcome"] == "not_registered"
    assert "register" in parsed["operator_action"].lower()


# --- Unsupported / too-old daemon fail closed ------------------------------


@pytest.mark.asyncio
async def test_capability_mismatch_fails_closed_with_guidance(monkeypatch):
    daemon = _FakeBindDaemon(
        result=_error_result("daemon_capability_mismatch", "local_workspace_unbound", retry=True)
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.workspace.bind",
        _base_args(confirmed=True, required_daemon_capability=2),
    )
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["outcome"] == "unsupported"
    assert "upgrade" in parsed["operator_action"].lower()


@pytest.mark.asyncio
async def test_daemon_without_workspace_bind_command_fails_closed(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT, supports_bind=False)
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=True))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["error_code"] == "daemon_capability_error"
    assert parsed["recovery"]["requested_command"] == WORKSPACE_BIND_COMMAND
    # Fail closed: no bind request dispatched to an incapable daemon.
    assert daemon.requests == []


@pytest.mark.asyncio
async def test_daemon_unavailable_dispatches_nothing(monkeypatch):
    class _Unreachable(_FakeBindDaemon):
        async def capabilities(self) -> dict:
            raise DaemonConnectionError("cannot reach bicameral-bot daemon")

    daemon = _Unreachable(result=BOUND_RESULT)
    _patch(monkeypatch, daemon)

    content = await server.call_tool("bicameral.workspace.bind", _base_args(confirmed=True))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["error_code"] == "daemon_unavailable"
    assert daemon.requests == []


# --- list_tools surfaces the tool ------------------------------------------


@pytest.mark.asyncio
async def test_workspace_bind_listed_when_daemon_supports_it(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT)
    _patch(monkeypatch, daemon)

    tools = await server.list_tools()
    assert "bicameral.workspace.bind" in {tool.name for tool in tools}


@pytest.mark.asyncio
async def test_workspace_bind_hidden_when_daemon_lacks_command(monkeypatch):
    daemon = _FakeBindDaemon(result=BOUND_RESULT, supports_bind=False)
    _patch(monkeypatch, daemon)

    tools = await server.list_tools()
    assert "bicameral.workspace.bind" not in {tool.name for tool in tools}


def _run(coro):
    import asyncio

    return asyncio.run(coro)
