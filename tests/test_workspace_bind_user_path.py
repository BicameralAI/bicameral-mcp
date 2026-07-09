"""MCP-side workspace.bind user-path conformance (mcp#706).

Covers the acceptance criteria for binding a hosted Project to a local folder:

  * the bind action is user-visible only when the daemon advertises the
    truthful ``workspace_binding_available`` capability (bicameral-bot#747);
  * MCP forwards a ``workspace.bind`` proposal and surfaces the daemon's
    success/failure without claiming binding authority itself (ADR-0005);
  * unsupported-daemon and daemon-unavailable states fail closed with
    actionable messaging and dispatch no request;
  * a rejected candidate path / repair-required outcome is surfaced verbatim
    and fail-closed (never coerced to success);
  * the local ``candidate_path`` reaches only the local daemon — it is never
    placed in the hosted-safe authority envelope, and never echoed back to the
    caller by the renderer.

The daemon is seamed off; these are deterministic, no-LLM tests. A live
integration check against the merged bot#747 daemon is reported in the PR body.
"""

from __future__ import annotations

import json

import pytest

import server
from daemon_client import DaemonConnectionError
from tool_request import MCP_TOOL_COMMANDS

BIND_TOOL = "bicameral.workspace.bind"
CANDIDATE_PATH = "/home/operator/code/secret-local-folder"

_BASE_ARGS = {
    "project_id": "proj-706",
    "candidate_path": CANDIDATE_PATH,
    "confirmed": True,
    "display_name": "Project 706",
    "project_slug": "proj-706-slug",
    "reason": "operator selected this folder",
}


class _BindDaemon:
    """Configurable fake daemon for the workspace.bind path."""

    def __init__(
        self,
        *,
        workspace_binding_available: bool = True,
        bind_response: dict | None = None,
        capabilities_error: Exception | None = None,
    ) -> None:
        self.requests: list[dict] = []
        self._available = workspace_binding_available
        self._bind_response = bind_response
        self._capabilities_error = capabilities_error

    async def capabilities(self) -> dict:
        if self._capabilities_error is not None:
            raise self._capabilities_error
        return {
            "toolrequest_protocol_version": server.TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
            "workspace_binding_available": self._available,
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        response = dict(self._bind_response or {})
        response.setdefault("request_id", tool_request["request_id"])
        response.setdefault("responded_at", "2026-07-09T00:00:00Z")
        return response


def _patch(monkeypatch, daemon: _BindDaemon) -> None:
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "operator-1")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


# ---------------------------------------------------------------------------
# Acceptance: bind action is user-visible ONLY under capability discovery.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_tool_visible_only_when_capability_available(monkeypatch):
    _patch(monkeypatch, _BindDaemon(workspace_binding_available=True))
    names = {tool.name for tool in await server.list_tools()}
    assert BIND_TOOL in names


@pytest.mark.asyncio
async def test_bind_tool_hidden_when_capability_unavailable(monkeypatch):
    _patch(monkeypatch, _BindDaemon(workspace_binding_available=False))
    names = {tool.name for tool in await server.list_tools()}
    assert BIND_TOOL not in names


# ---------------------------------------------------------------------------
# Acceptance: success surfaces the daemon outcome without MCP claiming authority.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_success_surfaces_daemon_outcome(monkeypatch):
    daemon = _BindDaemon(
        bind_response={
            "status": "ok",
            "result": {
                "status": "bound",
                "outcome": {
                    "project_id": "proj-706",
                    "state": "local_workspace_bound",
                    "display": {"display_name": "Project 706"},
                    "message": "Bound by the local daemon.",
                },
            },
        },
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "bound"
    assert parsed["bound"] is True
    assert parsed["project_id"] == "proj-706"
    assert parsed["workspace_binding_state"] == "local_workspace_bound"
    # MCP disclaims authority even on success.
    assert "does not itself bind" in parsed["authority_note"]

    # Exactly one proposal dispatched, carrying the transient proposal shape.
    assert len(daemon.requests) == 1
    params = daemon.requests[0]["command"]["params"]
    assert daemon.requests[0]["command"]["name"] == "workspace.bind"
    assert params["confirmed"] is True
    assert params["proposal"]["source_surface"] == "mcp"
    assert params["proposal"]["project_id"] == "proj-706"


@pytest.mark.asyncio
async def test_bind_confirmation_defaults_false_when_omitted(monkeypatch):
    daemon = _BindDaemon(bind_response={"status": "ok", "result": {"status": "bound"}})
    _patch(monkeypatch, daemon)

    args = {k: v for k, v in _BASE_ARGS.items() if k != "confirmed"}
    await server.call_tool(BIND_TOOL, args)

    params = daemon.requests[0]["command"]["params"]
    assert params["confirmed"] is False


# ---------------------------------------------------------------------------
# Acceptance: no local path crosses to Cloud or is persisted by MCP.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_candidate_path_local_only_and_never_echoed(monkeypatch):
    daemon = _BindDaemon(
        bind_response={
            "status": "ok",
            "result": {"status": "bound", "outcome": {"project_id": "proj-706"}},
        },
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    request = daemon.requests[0]

    # The path is carried ONLY inside the local proposal for the daemon.
    assert request["command"]["params"]["proposal"]["candidate_path"] == CANDIDATE_PATH
    # It must never appear in the hosted-safe authority/audit envelope.
    assert CANDIDATE_PATH not in json.dumps(request["authority"])
    # The safe display label is a basename, not an absolute path.
    assert "/" not in request["command"]["params"]["proposal"]["display"]["candidate_label"]
    # The renderer never echoes the local path back to the caller.
    assert CANDIDATE_PATH not in content[0].text


# ---------------------------------------------------------------------------
# Acceptance: unsupported daemon fails closed with actionable messaging.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_unsupported_daemon_fails_closed(monkeypatch):
    daemon = _BindDaemon(workspace_binding_available=False)
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["error_code"] == "daemon_capability_error"
    assert parsed["recovery"]["operator_action"]
    # Fail closed: no proposal ever leaves the client.
    assert daemon.requests == []


# ---------------------------------------------------------------------------
# Acceptance: daemon-unavailable fails closed with actionable messaging.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_daemon_unavailable_fails_closed(monkeypatch):
    daemon = _BindDaemon(
        capabilities_error=DaemonConnectionError("cannot reach bicameral-bot daemon"),
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["error_code"] == "daemon_unavailable"
    assert parsed["recovery"]["retryable"] is True
    assert daemon.requests == []


# ---------------------------------------------------------------------------
# Acceptance: rejected path / repair-required surfaced verbatim, fail-closed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_rejected_unsafe_path_fails_closed(monkeypatch):
    daemon = _BindDaemon(
        bind_response={
            "status": "rejected",
            "result": {
                "error": "unsafe_path",
                "project_id": "proj-706",
                "message": "candidate path failed local safety policy",
                "state": "local_workspace_unbound",
                "retry_after_repair": False,
            },
        },
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "rejected"
    assert parsed["bound"] is False
    assert parsed["error_kind"] == "unsafe_path"
    assert parsed["workspace_binding_state"] == "local_workspace_unbound"
    assert "unsafe" in parsed["operator_action"].lower()
    assert "no workspace binding was materialized" in parsed["fail_closed_note"]


@pytest.mark.asyncio
async def test_bind_repair_required_surfaces_state(monkeypatch):
    daemon = _BindDaemon(
        bind_response={
            "status": "rejected",
            "result": {
                "error": "repair_required",
                "project_id": "proj-706",
                "message": "binding is broken and must be repaired",
                "state": "local_workspace_repair_required",
                "retry_after_repair": True,
            },
        },
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "rejected"
    assert parsed["bound"] is False
    assert parsed["error_kind"] == "repair_required"
    assert parsed["workspace_binding_state"] == "local_workspace_repair_required"
    assert parsed["retry_after_repair"] is True


@pytest.mark.asyncio
async def test_bind_confirmation_missing_rejection_is_actionable(monkeypatch):
    daemon = _BindDaemon(
        bind_response={
            "status": "rejected",
            "result": {
                "error": "confirmation_missing",
                "project_id": "proj-706",
                "message": "operator confirmation required",
                "state": "local_workspace_unbound",
                "retry_after_repair": False,
            },
        },
    )
    _patch(monkeypatch, daemon)

    content = await server.call_tool(BIND_TOOL, dict(_BASE_ARGS))
    parsed = json.loads(content[0].text)

    assert parsed["error_kind"] == "confirmation_missing"
    assert "confirmed=true" in parsed["operator_action"]
