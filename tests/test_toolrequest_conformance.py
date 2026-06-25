"""Deterministic ToolRequest conformance gate for the MCP thin client (mcp#555).

This is the deterministic replacement for the shelved live-agent e2e PR gate
(`v0-user-flow-e2e`). It validates the bot-owned ToolRequest (V1) contract that
the thin client marshals, with the daemon seamed off and **no LLM in the loop**:

  * well-formed ToolRequest construction for *every* MCP tool,
  * typed-state passthrough (the spec's binding/compliance/scope states survive
    the client unmodified — never coerced to success or escalated to blocking),
  * the read-model non-mutation boundary, and
  * replay of recorded daemon ToolResponse contract fixtures through the
    response renderers.

Grounding: `bicameral-bot/docs/specs/bot-mcp-data-flow-runtime-architecture.md`
(merged #255). The thin client is a transport surface; it can never itself
mutate ledger/governance state — so the non-mutation assertions here pin the
client's *observable* boundary (which command it emits, and that it emits at
most one, and that a failure emits none), not a deeper authority guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server
from daemon_client import DaemonConnectionError
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION


@pytest.fixture(autouse=True)
def _reset_approval_gate():
    """Ensure the module-level approval gate is clean between conformance tests."""
    server._approval_gate.clear()
    yield
    server._approval_gate.clear()


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "toolresponses"

# Read-model MCP tools: per the data-flow spec these are read-only and must
# never drive a mutating canonical event. Everything else may append events.
READ_MODEL_TOOLS = {
    "bicameral.preflight",
    "bicameral.binding.inspect",
    "bicameral.history",
    "bicameral.search",
}


class _CommandSpec:
    """Per-tool conformance expectation."""

    __slots__ = ("args", "command", "expected_params", "required")

    def __init__(
        self,
        *,
        command: str,
        args: dict,
        expected_params: set[str],
        required: set[str],
    ) -> None:
        self.command = command
        self.args = args
        self.expected_params = expected_params
        self.required = required


# Control keys (routed to the authority context, stripped from command params)
# and an extraneous key (allowlisted away) are seeded into every args dict so
# the param-shaping AND control-plane-routing contracts are exercised per command.
_CONTROL_AND_NOISE = {
    "actor_id": "control-actor-routed-to-authority",
    "session_id": "control-session-routed-to-authority",
    "workspace": "/control/workspace/routed",
    "policy_scope": ["scope-a", "scope-b"],
    "totally_unknown_param": "should-be-allowlisted-away",
}

COMMAND_SPECS: dict[str, _CommandSpec] = {
    "bicameral.ingest": _CommandSpec(
        command="ingest.submit_local",
        args={
            "source_uri": "local://meeting.md",
            "source_type": "meeting",
            "title": "Backport policy",
            "description": "cherry-pick is default",
            "level": "decision",
            **_CONTROL_AND_NOISE,
        },
        expected_params={
            "source_uri",
            "source_type",
            "label",
            "title",
            "description",
            "level",
            "snapshot_content",
            "evidence",
        },
        required={"source_uri", "source_type", "title", "description"},
    ),
    "bicameral.preflight": _CommandSpec(
        command="preflight.run",
        args={
            "files": ["app/src/lib/git/cherry-pick.ts"],
            "symbols": ["applyCherryPick"],
            "branch": "feature/x",
            **_CONTROL_AND_NOISE,
        },
        expected_params={"files", "symbols", "diff_context", "branch"},
        required=set(),
    ),
    "bicameral.bind": _CommandSpec(
        command="binding.create",
        args={
            "decision_or_candidate_id": "DEC-1",
            "bindings": [{"symbol": "applyCherryPick"}],
            "commit_sha": "e6c50fb",
            **_CONTROL_AND_NOISE,
        },
        expected_params={"decision_or_candidate_id", "bindings", "commit_sha", "ref_name"},
        required={"decision_or_candidate_id", "bindings"},
    ),
    "bicameral.binding.inspect": _CommandSpec(
        command="binding.inspect",
        args={"decision_or_candidate_id": "DEC-1", **_CONTROL_AND_NOISE},
        expected_params={"decision_or_candidate_id", "commit_sha"},
        required={"decision_or_candidate_id"},
    ),
    "bicameral.evidence.refresh": _CommandSpec(
        command="evidence.refresh",
        args={"decision_id": "DEC-7", **_CONTROL_AND_NOISE},
        expected_params={"decision_id"},
        required={"decision_id"},
    ),
    "bicameral.review.accept_candidate": _CommandSpec(
        command="review.accept_candidate",
        args={"target_id": "cand-1", "reason": "ok", **_CONTROL_AND_NOISE},
        expected_params={"target_id", "reason"},
        required={"target_id"},
    ),
    "bicameral.review.reject_candidate": _CommandSpec(
        command="review.reject_candidate",
        args={"target_id": "cand-1", "reason": "no", **_CONTROL_AND_NOISE},
        expected_params={"target_id", "reason"},
        required={"target_id"},
    ),
    "bicameral.review.approve_signoff": _CommandSpec(
        command="review.approve_signoff",
        args={"target_id": "DEC-7", **_CONTROL_AND_NOISE},
        expected_params={"target_id", "reason"},
        required={"target_id"},
    ),
    "bicameral.review.reject_signoff": _CommandSpec(
        command="review.reject_signoff",
        args={"target_id": "DEC-7", "reason": "needs review", **_CONTROL_AND_NOISE},
        expected_params={"target_id", "reason"},
        required={"target_id"},
    ),
    "bicameral.review.resolve_compliance": _CommandSpec(
        command="review.resolve_compliance",
        args={
            "target_id": "DEC-7",
            "compliance_verdict": "reflected",
            "reason": "verified",
            **_CONTROL_AND_NOISE,
        },
        expected_params={"target_id", "compliance_verdict", "reason"},
        required={"target_id", "compliance_verdict"},
    ),
    "bicameral.history": _CommandSpec(
        command="history.list",
        args={"decision_id": "DEC-7", "include_events": True, **_CONTROL_AND_NOISE},
        expected_params={"decision_id", "include_events", "include_bindings", "since"},
        required=set(),
    ),
    "bicameral.lookup": _CommandSpec(
        command="lookup.query",
        args={"files": ["src/main.py"], "scope": "pre_work", **_CONTROL_AND_NOISE},
        expected_params={"files", "symbols", "scope", "include_context"},
        required=set(),
    ),
    "bicameral.request_correction": _CommandSpec(
        command="correction.request",
        args={
            "target_id": "DEC-9",
            "correction_type": "amend",
            "reason": "constraint outdated",
            **_CONTROL_AND_NOISE,
        },
        expected_params={"target_id", "correction_type", "reason", "context"},
        required={"target_id", "correction_type", "reason"},
    ),
    "bicameral.search": _CommandSpec(
        command="search.query",
        args={"query": "cherry-pick", "scope": "decisions", **_CONTROL_AND_NOISE},
        expected_params={"query", "scope", "filters", "limit"},
        required={"query"},
    ),
    "bicameral.request_correction": _CommandSpec(
        command="correction.request",
        args={
            "packet_id": "pkt-conformance",
            "correction_request": "fix drift in binding",
            "reason": "verified locally",
            **_CONTROL_AND_NOISE,
        },
        expected_params={"packet_id", "excerpt", "diff", "correction_request", "reason"},
        required=set(),
    ),
}

_CONTROL_KEYS = {"actor_id", "session_id", "workspace", "policy_scope"}


class _RecordingDaemon:
    """Capability-compatible fake daemon that records dispatched ToolRequests.

    The ``response_for`` hook lets a test choose the ToolResponse per command
    (e.g. to inject a typed non-verified binding state). Default is a generic
    ``status: ok`` echo.
    """

    def __init__(self, *, response_for=None, protocol_version: str | None = None) -> None:
        self.protocol_version = protocol_version or TOOLREQUEST_PROTOCOL_VERSION
        self.requests: list[dict] = []
        self._response_for = response_for

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        command = tool_request["command"]["name"]
        if self._response_for is not None:
            override = self._response_for(command, tool_request)
            if override is not None:
                return {"request_id": tool_request["request_id"], **override}
        base = {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": command},
            "responded_at": "2026-06-22T00:00:00Z",
        }
        if command == "preflight.run":
            base["staged"] = {
                "lookup": {"status": "completed", "decision_refs": [], "limitations": []},
                "session_directive": {"mode": "continue"},
            }
        return base


def _patch_daemon(monkeypatch, daemon: _RecordingDaemon) -> None:
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "conformance-actor")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


# ---------------------------------------------------------------------------
# Completeness — every command in the production map has a conformance spec and
# a recorded contract fixture. Catches a new tool landing without coverage.
# ---------------------------------------------------------------------------


def test_specs_cover_every_production_command():
    assert set(COMMAND_SPECS) == set(MCP_TOOL_COMMANDS)
    assert {spec.command for spec in COMMAND_SPECS.values()} == set(MCP_TOOL_COMMANDS.values())


def test_fixture_exists_for_every_command():
    present = {p.stem for p in FIXTURE_DIR.glob("*.json")}
    expected = set(MCP_TOOL_COMMANDS.values())
    assert expected <= present, f"missing contract fixtures for: {expected - present}"


# ---------------------------------------------------------------------------
# Layer 1 — well-formed ToolRequest for every MCP tool.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(COMMAND_SPECS))
async def test_tool_emits_well_formed_toolrequest(tool_name, monkeypatch):
    spec = COMMAND_SPECS[tool_name]
    daemon = _RecordingDaemon()
    _patch_daemon(monkeypatch, daemon)

    # request_correction requires prior approval gate grant.
    if tool_name == "bicameral.request_correction":
        scope_args = {
            k: v
            for k, v in spec.args.items()
            if k in ("packet_id", "excerpt", "diff", "correction_request")
        }
        await server.call_tool("bicameral.request_correction.approve", scope_args)

    content = await server.call_tool(tool_name, dict(spec.args))

    # Exactly one ToolRequest dispatched per tool call.
    assert len(daemon.requests) == 1
    request = daemon.requests[0]

    # Envelope shape is the v2 contract: request_id, command, authority, issued_at.
    assert set(request) == {"request_id", "command", "authority", "issued_at"}
    assert isinstance(request["request_id"], str) and request["request_id"]
    assert request["issued_at"].endswith("Z")

    # Command maps to the canonical bot command.
    command = request["command"]
    assert command["name"] == spec.command

    # Params: control keys stripped, unknown keys allowlisted away, required present.
    params = command["params"]
    assert set(params) <= spec.expected_params
    assert _CONTROL_KEYS.isdisjoint(params)
    assert "totally_unknown_param" not in params
    assert spec.required <= set(params)

    # Authority context is MCP-session shaped, and the control keys present in
    # the tool arguments are routed here (control plane) rather than into params.
    authority = request["authority"]
    assert authority["auth_method"] == "mcp_session"
    assert authority["actor_id"] == "control-actor-routed-to-authority"
    assert authority["session_id"] == "control-session-routed-to-authority"
    assert authority["workspace"] == "/control/workspace/routed"
    assert authority["policy_scope"] == ["scope-a", "scope-b"]
    assert authority["audit_metadata"]["surface"] == "mcp"
    assert authority["audit_metadata"]["mcp_tool"] == tool_name

    # Response is always renderable JSON.
    parsed = json.loads(content[0].text)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Layer 2 — typed-state passthrough. The thin client must surface the daemon's
# typed states verbatim; it may never coerce a non-verified/typed state into
# success or escalate it into a blocking directive.
# ---------------------------------------------------------------------------

BINDING_TYPED_STATES = [
    "unsupported",
    "stale",
    "ambiguous",
    "not_indexed",
    "approximate",
    "snapshot_mismatch",
    "unavailable",
    "not_found",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", BINDING_TYPED_STATES)
async def test_binding_create_typed_state_passthrough(state, monkeypatch):
    def respond(command, _req):
        if command == "binding.create":
            return {
                "status": state,
                "result": {"evidence_state": state, "verified": False},
                "responded_at": "2026-06-22T00:00:00Z",
            }
        return None

    daemon = _RecordingDaemon(response_for=respond)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.bind",
        {"decision_or_candidate_id": "DEC-1", "bindings": [{"symbol": "x"}]},
    )
    parsed = json.loads(content[0].text)

    # Typed state survives unmodified; never silently promoted to verified/ok.
    assert parsed["status"] == state
    assert parsed["result"]["verified"] is False
    assert parsed["result"]["evidence_state"] == state


@pytest.mark.asyncio
async def test_resolve_compliance_deferred_passthrough(monkeypatch):
    def respond(command, _req):
        if command == "review.resolve_compliance":
            return {
                "status": "unsupported",
                "result": {"deferred": True, "capability": "review.resolve_compliance"},
                "responded_at": "2026-06-22T00:00:00Z",
            }
        return None

    daemon = _RecordingDaemon(response_for=respond)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.review.resolve_compliance",
        {"target_id": "DEC-7", "compliance_verdict": "reflected"},
    )
    parsed = json.loads(content[0].text)

    # V1 contract: compliance resolution is typed unsupported/deferred, not "ok".
    assert parsed["status"] == "unsupported"
    assert parsed["result"]["deferred"] is True


EVIDENCE_REFRESH_TYPED_STATES = [
    "current",
    "content_changed",
    "target_missing",
    "graph_unavailable",
    "graph_not_configured",
    "unsupported_workspace",
    "needs_index_refresh",
    "ineligible_decision",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", EVIDENCE_REFRESH_TYPED_STATES)
async def test_evidence_refresh_typed_currentness_passthrough(state, monkeypatch):
    def respond(command, _req):
        if command == "evidence.refresh":
            return {
                "status": state,
                "result": {
                    "decision_id": "DEC-7",
                    "currentness": state,
                    "signoff_mutated": False,
                    "compliance_mutated": False,
                    "binding_evidence_mutated": False,
                },
                "responded_at": "2026-06-23T00:00:00Z",
            }
        return None

    daemon = _RecordingDaemon(response_for=respond)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool("bicameral.evidence.refresh", {"decision_id": "DEC-7"})
    parsed = json.loads(content[0].text)

    assert parsed["status"] == state
    assert parsed["result"]["currentness"] == state
    assert parsed["result"]["signoff_mutated"] is False
    assert parsed["result"]["compliance_mutated"] is False
    assert parsed["result"]["binding_evidence_mutated"] is False


# ---------------------------------------------------------------------------
# Layer 3 — read-model non-mutation boundary.
# ---------------------------------------------------------------------------


def test_read_model_tools_map_only_to_read_commands():
    read_commands = {MCP_TOOL_COMMANDS[t] for t in READ_MODEL_TOOLS}
    assert read_commands == {
        "preflight.run",
        "binding.inspect",
        "history.list",
        "search.query",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(READ_MODEL_TOOLS))
async def test_read_model_tool_dispatches_exactly_one_read_command(tool_name, monkeypatch):
    spec = COMMAND_SPECS[tool_name]
    daemon = _RecordingDaemon()
    _patch_daemon(monkeypatch, daemon)

    await server.call_tool(tool_name, dict(spec.args))

    assert len(daemon.requests) == 1
    emitted = daemon.requests[0]["command"]["name"]
    assert emitted == spec.command
    assert emitted in {"preflight.run", "binding.inspect", "history.list", "search.query"}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", sorted(COMMAND_SPECS))
async def test_handshake_failure_dispatches_no_request(tool_name, monkeypatch):
    """A failed handshake must append nothing — no ToolRequest leaves the client."""

    class _Unreachable(_RecordingDaemon):
        async def capabilities(self) -> dict:
            raise DaemonConnectionError("cannot reach bicameral-bot daemon")

    daemon = _Unreachable()
    _patch_daemon(monkeypatch, daemon)

    # request_correction requires prior approval gate grant.
    if tool_name == "bicameral.request_correction":
        spec = COMMAND_SPECS[tool_name]
        scope_args = {
            k: v
            for k, v in spec.args.items()
            if k in ("packet_id", "excerpt", "diff", "correction_request")
        }
        await server.call_tool("bicameral.request_correction.approve", scope_args)

    content = await server.call_tool(tool_name, dict(COMMAND_SPECS[tool_name].args))
    parsed = json.loads(content[0].text)

    assert parsed["status"] == "error"
    assert parsed["error_code"] == "daemon_unavailable"
    assert daemon.requests == []


# ---------------------------------------------------------------------------
# Layer 4 — recorded contract-fixture replay through the response renderers.
# ---------------------------------------------------------------------------


def _load_fixture(command: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{command}.json").read_text())


@pytest.mark.parametrize("command", sorted(set(MCP_TOOL_COMMANDS.values())))
def test_contract_fixture_renders_through_renderer(command):
    from responses import (
        format_correction_response,
        format_lookup_response,
        format_preflight_response,
        format_tool_response,
    )

    payload = _load_fixture(command)
    assert payload["status"]  # fixture carries an explicit status
    assert payload["request_id"]

    if command == "preflight.run":
        renderer = format_preflight_response
    elif command == "lookup.query":
        renderer = format_lookup_response
    elif command == "correction.request":
        renderer = format_correction_response
    else:
        renderer = format_tool_response
    content = renderer(payload)
    rendered = json.loads(content.text)
    assert isinstance(rendered, dict)


def test_preflight_fixture_surfaces_source_only_limitation():
    payload = _load_fixture("preflight.run")
    from responses import format_preflight_response

    rendered = json.loads(format_preflight_response(payload).text)
    lookup = rendered["stages"]["lookup"]
    assert lookup["status"] == "completed"
    # Source-only graph-degraded limitation is surfaced, not swallowed.
    assert any("source-only" in limitation for limitation in lookup["limitations"])


def test_search_and_history_fixtures_type_binding_scope_unsupported():
    for command in ("search.query", "history.list"):
        payload = _load_fixture(command)
        assert payload["result"]["binding_scope"]["status"] == "unsupported"
