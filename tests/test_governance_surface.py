"""Tests for MCP governance inbox and contradiction resolution surface.

Covers:
- Governance tool schemas exposed correctly
- ToolRequest shape for governance commands
- Server routing through governance formatters
- Authorized resolution flow (daemon accepts)
- Unauthorized resolution flow (daemon rejects)
- Inbox deduplication of ContradictionReport IDs
- Inspect rendering
- Command param shaping (control keys stripped, only allowed keys forwarded)
"""

from __future__ import annotations

import json

import pytest

import server
from governance_surface import (
    format_governance_inbox,
    format_governance_inspect,
    format_governance_resolve,
)
from tool_request import MCP_TOOL_COMMANDS, build_tool_request
from tool_schemas import tool_for_name
from version import TOOLREQUEST_PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Fake daemon client for governance integration tests
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(
        self,
        *,
        protocol_version: str = TOOLREQUEST_PROTOCOL_VERSION,
        response_override: dict | None = None,
    ):
        self.protocol_version = protocol_version
        self.requests: list[dict] = []
        self.response_override = response_override

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        if self.response_override is not None:
            return {
                "request_id": tool_request["request_id"],
                **self.response_override,
            }
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": tool_request["command"]["name"]},
            "responded_at": "2026-06-26T00:00:00Z",
        }


# ---------------------------------------------------------------------------
# Tool schema tests
# ---------------------------------------------------------------------------


def test_governance_inbox_schema_exists():
    tool = tool_for_name("bicameral.governance.inbox")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "status_filter" in props
    assert props["status_filter"]["type"] == "array"
    assert "limit" in props
    assert props["limit"]["type"] == "integer"
    assert "actor_id" in props


def test_governance_inspect_schema_exists():
    tool = tool_for_name("bicameral.governance.inspect")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "report_id" in props
    assert tool.inputSchema["required"] == ["report_id"]


def test_governance_resolve_schema_exists():
    tool = tool_for_name("bicameral.governance.resolve")
    assert tool is not None
    props = tool.inputSchema["properties"]
    assert "report_id" in props
    assert "action" in props
    assert props["action"]["enum"] == ["resolve", "acknowledge", "dismiss", "route"]
    assert "reason" in props
    assert "route_to" in props
    assert set(tool.inputSchema["required"]) == {"report_id", "action"}


# ---------------------------------------------------------------------------
# MCP_TOOL_COMMANDS mapping tests
# ---------------------------------------------------------------------------


def test_governance_commands_mapped():
    assert MCP_TOOL_COMMANDS["bicameral.governance.inbox"] == "governance.inbox.list"
    assert MCP_TOOL_COMMANDS["bicameral.governance.inspect"] == "governance.inspect"
    assert MCP_TOOL_COMMANDS["bicameral.governance.resolve"] == "governance.resolve_contradiction"


# ---------------------------------------------------------------------------
# ToolRequest shape tests
# ---------------------------------------------------------------------------


def test_governance_inbox_toolrequest_shape():
    request = build_tool_request(
        command_name="governance.inbox.list",
        params={
            "status_filter": ["open"],
            "limit": 10,
            "actor_id": "should-be-stripped",
            "session_id": "should-be-stripped",
        },
        authority={"actor_id": "owner-1", "auth_method": "mcp_session"},
    )

    assert request["command"]["name"] == "governance.inbox.list"
    assert request["command"]["params"] == {"status_filter": ["open"], "limit": 10}
    assert "actor_id" not in request["command"]["params"]


def test_governance_inspect_toolrequest_shape():
    request = build_tool_request(
        command_name="governance.inspect",
        params={
            "report_id": "CR-001",
            "workspace": "should-be-stripped",
        },
        authority={"actor_id": "owner-1", "auth_method": "mcp_session"},
    )

    assert request["command"]["name"] == "governance.inspect"
    assert request["command"]["params"] == {"report_id": "CR-001"}


def test_governance_resolve_toolrequest_shape():
    request = build_tool_request(
        command_name="governance.resolve_contradiction",
        params={
            "report_id": "CR-002",
            "action": "dismiss",
            "reason": "false positive",
            "policy_scope": ["should-be-stripped"],
        },
        authority={"actor_id": "owner-1", "auth_method": "mcp_session"},
    )

    assert request["command"]["name"] == "governance.resolve_contradiction"
    assert request["command"]["params"] == {
        "report_id": "CR-002",
        "action": "dismiss",
        "reason": "false positive",
    }


def test_governance_resolve_route_includes_route_to():
    request = build_tool_request(
        command_name="governance.resolve_contradiction",
        params={
            "report_id": "CR-003",
            "action": "route",
            "route_to": "security-team",
        },
        authority={"actor_id": "owner-1", "auth_method": "mcp_session"},
    )

    assert request["command"]["params"] == {
        "report_id": "CR-003",
        "action": "route",
        "route_to": "security-team",
    }


# ---------------------------------------------------------------------------
# Formatter unit tests
# ---------------------------------------------------------------------------


def test_format_governance_inbox_deduplicates_by_report_id():
    response = {
        "status": "ok",
        "request_id": "req-inbox-1",
        "findings": [
            {
                "report_id": "CR-001",
                "status": "open",
                "reason_code": "active_contradiction",
                "affected_refs": ["DEC-1"],
                "evidence_refs": ["EV-1"],
                "allowed_actions": ["resolve", "acknowledge", "dismiss"],
                "summary": "Contradicts DEC-1",
            },
            {
                "report_id": "CR-001",
                "status": "open",
                "reason_code": "active_contradiction",
                "affected_refs": ["DEC-1"],
                "evidence_refs": ["EV-1"],
                "allowed_actions": ["resolve", "acknowledge", "dismiss"],
                "summary": "Duplicate",
            },
            {
                "report_id": "CR-002",
                "status": "acknowledged",
                "reason_code": "active_contradiction",
                "affected_refs": ["DEC-2"],
                "evidence_refs": [],
                "allowed_actions": ["resolve", "dismiss"],
            },
        ],
    }

    content = format_governance_inbox(response)
    output = json.loads(content.text)

    assert output["status"] == "ok"
    assert output["request_id"] == "req-inbox-1"
    assert output["total"] == 2
    assert len(output["findings"]) == 2

    ids = [f["report_id"] for f in output["findings"]]
    assert ids == ["CR-001", "CR-002"]
    assert output["findings"][0]["summary"] == "Contradicts DEC-1"
    assert "summary" not in output["findings"][1]


def test_format_governance_inbox_empty():
    response = {"status": "ok", "request_id": "req-empty", "findings": []}

    content = format_governance_inbox(response)
    output = json.loads(content.text)

    assert output["total"] == 0
    assert output["findings"] == []


def test_format_governance_inspect_renders_detail():
    response = {
        "status": "ok",
        "request_id": "req-inspect-1",
        "finding": {
            "report_id": "CR-001",
            "status": "open",
            "reason_code": "active_contradiction",
            "affected_refs": ["DEC-1", "DEC-3"],
            "evidence_refs": ["EV-1"],
            "allowed_actions": ["resolve", "acknowledge", "dismiss", "route"],
            "summary": "Contradicts DEC-1 and DEC-3",
            "detail": "Full explanation of the contradiction.",
            "created_at": "2026-06-25T10:00:00Z",
        },
    }

    content = format_governance_inspect(response)
    output = json.loads(content.text)

    assert output["status"] == "ok"
    assert output["finding"]["report_id"] == "CR-001"
    assert output["finding"]["detail"] == "Full explanation of the contradiction."
    assert output["finding"]["created_at"] == "2026-06-25T10:00:00Z"
    assert output["finding"]["allowed_actions"] == [
        "resolve",
        "acknowledge",
        "dismiss",
        "route",
    ]


def test_format_governance_inspect_omits_absent_optional_fields():
    response = {
        "status": "ok",
        "request_id": "req-inspect-2",
        "finding": {
            "report_id": "CR-002",
            "status": "acknowledged",
            "reason_code": "active_contradiction",
            "affected_refs": [],
            "evidence_refs": [],
            "allowed_actions": ["resolve"],
        },
    }

    content = format_governance_inspect(response)
    output = json.loads(content.text)

    assert "summary" not in output["finding"]
    assert "detail" not in output["finding"]
    assert "created_at" not in output["finding"]


def test_format_governance_resolve_authorized():
    response = {
        "status": "ok",
        "request_id": "req-resolve-1",
        "result": {
            "report_id": "CR-001",
            "action": "resolve",
            "accepted": True,
            "message": "Finding resolved by owner.",
        },
    }

    content = format_governance_resolve(response)
    output = json.loads(content.text)

    assert output["status"] == "ok"
    assert output["report_id"] == "CR-001"
    assert output["action"] == "resolve"
    assert output["accepted"] is True
    assert output["message"] == "Finding resolved by owner."
    assert "error_code" not in output


def test_format_governance_resolve_unauthorized():
    response = {
        "status": "unauthorized",
        "request_id": "req-resolve-2",
        "error_code": "unauthorized",
        "result": {
            "report_id": "CR-001",
            "action": "resolve",
            "accepted": False,
            "message": "Actor is not Product Owner or delegate.",
        },
    }

    content = format_governance_resolve(response)
    output = json.loads(content.text)

    assert output["status"] == "unauthorized"
    assert output["error_code"] == "unauthorized"
    assert output["accepted"] is False
    assert output["report_id"] == "CR-001"


def test_format_governance_resolve_dismiss():
    response = {
        "status": "ok",
        "request_id": "req-dismiss-1",
        "result": {
            "report_id": "CR-003",
            "action": "dismiss",
            "accepted": True,
        },
    }

    content = format_governance_resolve(response)
    output = json.loads(content.text)

    assert output["action"] == "dismiss"
    assert output["accepted"] is True
    assert "message" not in output


# ---------------------------------------------------------------------------
# Server integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_governance_inbox_routes_through_daemon(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "ok",
            "findings": [
                {
                    "report_id": "CR-100",
                    "status": "open",
                    "reason_code": "active_contradiction",
                    "affected_refs": ["DEC-10"],
                    "evidence_refs": ["EV-5"],
                    "allowed_actions": ["resolve", "acknowledge"],
                    "summary": "Active finding",
                },
            ],
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "owner-1")

    content = await server.call_tool(
        "bicameral.governance.inbox",
        {"status_filter": ["open"]},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert response["total"] == 1
    assert response["findings"][0]["report_id"] == "CR-100"
    assert fake.requests[0]["command"]["name"] == "governance.inbox.list"
    assert fake.requests[0]["command"]["params"] == {"status_filter": ["open"]}
    assert fake.requests[0]["authority"]["auth_method"] == "mcp_session"


@pytest.mark.asyncio
async def test_governance_inspect_routes_through_daemon(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "ok",
            "finding": {
                "report_id": "CR-200",
                "status": "open",
                "reason_code": "active_contradiction",
                "affected_refs": ["DEC-20"],
                "evidence_refs": ["EV-10"],
                "allowed_actions": ["resolve", "dismiss"],
                "summary": "Inspection result",
                "detail": "Full detail.",
                "created_at": "2026-06-25T00:00:00Z",
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "owner-1")

    content = await server.call_tool(
        "bicameral.governance.inspect",
        {"report_id": "CR-200"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert response["finding"]["report_id"] == "CR-200"
    assert response["finding"]["detail"] == "Full detail."
    assert fake.requests[0]["command"]["name"] == "governance.inspect"
    assert fake.requests[0]["command"]["params"] == {"report_id": "CR-200"}


@pytest.mark.asyncio
async def test_governance_resolve_authorized_routes_through_daemon(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "ok",
            "result": {
                "report_id": "CR-300",
                "action": "resolve",
                "accepted": True,
                "message": "Resolved by owner.",
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "owner-1")

    content = await server.call_tool(
        "bicameral.governance.resolve",
        {"report_id": "CR-300", "action": "resolve", "reason": "verified"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert response["accepted"] is True
    assert response["report_id"] == "CR-300"
    assert fake.requests[0]["command"]["name"] == "governance.resolve_contradiction"
    assert fake.requests[0]["command"]["params"] == {
        "report_id": "CR-300",
        "action": "resolve",
        "reason": "verified",
    }
    assert fake.requests[0]["authority"]["auth_method"] == "mcp_session"
    assert fake.requests[0]["authority"]["actor_id"] == "owner-1"
    assert fake.requests[0]["authority"]["audit_metadata"]["surface"] == "mcp"
    assert (
        fake.requests[0]["authority"]["audit_metadata"]["mcp_tool"]
        == "bicameral.governance.resolve"
    )


@pytest.mark.asyncio
async def test_governance_resolve_unauthorized_renders_rejection(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "unauthorized",
            "error_code": "unauthorized",
            "result": {
                "report_id": "CR-300",
                "action": "resolve",
                "accepted": False,
                "message": "Actor agent-rogue is not Product Owner or delegate.",
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-rogue")

    content = await server.call_tool(
        "bicameral.governance.resolve",
        {"report_id": "CR-300", "action": "resolve"},
    )
    response = json.loads(content[0].text)

    assert response["status"] == "unauthorized"
    assert response["error_code"] == "unauthorized"
    assert response["accepted"] is False
    assert len(fake.requests) == 1


@pytest.mark.asyncio
async def test_governance_resolve_acknowledge_flow(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "ok",
            "result": {
                "report_id": "CR-400",
                "action": "acknowledge",
                "accepted": True,
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "owner-1")

    content = await server.call_tool(
        "bicameral.governance.resolve",
        {"report_id": "CR-400", "action": "acknowledge"},
    )
    response = json.loads(content[0].text)

    assert response["accepted"] is True
    assert response["action"] == "acknowledge"
    assert fake.requests[0]["command"]["params"]["action"] == "acknowledge"


@pytest.mark.asyncio
async def test_server_governance_resolve_route_includes_route_to(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "ok",
            "result": {
                "report_id": "CR-500",
                "action": "route",
                "accepted": True,
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "owner-1")

    content = await server.call_tool(
        "bicameral.governance.resolve",
        {"report_id": "CR-500", "action": "route", "route_to": "security-team"},
    )
    response = json.loads(content[0].text)

    assert response["accepted"] is True
    assert fake.requests[0]["command"]["params"] == {
        "report_id": "CR-500",
        "action": "route",
        "route_to": "security-team",
    }


@pytest.mark.asyncio
async def test_governance_tools_strip_control_keys_from_params(monkeypatch):
    fake = _FakeClient(
        response_override={
            "status": "ok",
            "findings": [],
        }
    )
    monkeypatch.setattr(server, "_client", lambda: fake)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "owner-1")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

    await server.call_tool(
        "bicameral.governance.inbox",
        {
            "status_filter": ["open"],
            "actor_id": "should-strip",
            "session_id": "should-strip",
            "workspace": "/repo",
            "policy_scope": ["scope-1"],
        },
    )

    params = fake.requests[0]["command"]["params"]
    assert "actor_id" not in params
    assert "session_id" not in params
    assert "workspace" not in params
    assert "policy_scope" not in params
    assert params == {"status_filter": ["open"]}

    authority = fake.requests[0]["authority"]
    assert authority["actor_id"] == "should-strip"
    assert authority["workspace"] == "/repo"
