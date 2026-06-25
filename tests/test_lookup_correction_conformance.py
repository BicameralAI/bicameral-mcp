"""Conformance tests for lookup/correction capability gating (issue #639).

Proves:
1. MCP gates bicameral.lookup and bicameral.request_correction on daemon
   capability reports (unsupported, deferred, unavailable states).
2. MCP maps lookup/correction to canonical bot ToolRequests.
3. MCP creates no local SourceSnapshot, binding, or compliance artifact.

Acceptance criteria:
AC-1: Gate lookup and request_correction on daemon capability reports.
AC-2: Render daemon unavailable, unsupported, and deferred states explicitly.
AC-3: Conformance: MCP maps lookup/correction to bot ToolRequests.
AC-4: No-local-state: MCP creates no SourceSnapshot, binding, or compliance
      artifact.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

import server
from daemon_client import (
    CapabilityReport,
    DaemonCapabilityError,
    DaemonConnectionError,
)
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS, build_tool_request
from version import TOOLREQUEST_PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Fake daemon clients
# ---------------------------------------------------------------------------


class _FullCapabilityDaemon:
    """Daemon that supports all commands including lookup/correction."""

    def __init__(self, *, response_override: dict[str, Any] | None = None):
        self.requests: list[dict[str, Any]] = []
        self.response_override = response_override

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(SUPPORTED_COMMANDS),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        if self.response_override:
            return {
                "request_id": tool_request["request_id"],
                **self.response_override,
            }
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": tool_request["command"]["name"]},
            "responded_at": "2026-06-25T00:00:00Z",
        }


class _NoLookupDaemon:
    """Daemon that does NOT advertise lookup.query or correction.request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        # Advertise all commands except lookup.query and correction.request
        commands = [
            c for c in SUPPORTED_COMMANDS if c not in ("lookup.query", "correction.request")
        ]
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {},
            "responded_at": "2026-06-25T00:00:00Z",
        }


class _DeferredLookupDaemon:
    """Daemon that advertises commands but defers lookup/correction at runtime."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(SUPPORTED_COMMANDS),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        cmd = tool_request["command"]["name"]
        if cmd in ("lookup.query", "correction.request"):
            raise DaemonCapabilityError(f"deferred: daemon has not yet implemented {cmd}")
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {},
            "responded_at": "2026-06-25T00:00:00Z",
        }


class _UnreachableDaemon:
    """Daemon that is unreachable (connection refused)."""

    async def capabilities(self) -> dict[str, Any]:
        raise DaemonConnectionError(
            "cannot reach bicameral-bot daemon at http://127.0.0.1:37373",
            daemon_endpoint="http://127.0.0.1:37373",
        )


# ---------------------------------------------------------------------------
# AC-1: Gate lookup and request_correction on daemon capability reports
# ---------------------------------------------------------------------------


class TestCapabilityGating:
    """AC-1: Lookup and correction are gated on daemon capability reports."""

    @pytest.mark.asyncio
    async def test_lookup_gated_when_not_advertised(self, monkeypatch):
        """lookup.query not in supported_commands -> capability error."""
        daemon = _NoLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.lookup", {"files": ["src/main.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_capability_error"
        assert daemon.requests == []

    @pytest.mark.asyncio
    async def test_correction_gated_when_not_advertised(self, monkeypatch):
        """correction.request not in supported_commands -> capability error."""
        daemon = _NoLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "dec-1", "correction_type": "amend", "reason": "test"},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_capability_error"
        assert daemon.requests == []

    @pytest.mark.asyncio
    async def test_lookup_succeeds_when_advertised(self, monkeypatch):
        """lookup.query in supported_commands -> request dispatched."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "recall_packet": {
                    "searched_sources": ["decisions"],
                    "corpus_version": "2026-06-25",
                    "matches": [],
                    "unknown_scope": [],
                    "allowed_next_actions": ["proceed"],
                },
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.lookup", {"files": ["src/main.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "ok"
        assert len(daemon.requests) == 1
        assert daemon.requests[0]["command"]["name"] == "lookup.query"

    @pytest.mark.asyncio
    async def test_correction_succeeds_when_advertised(self, monkeypatch):
        """correction.request in supported_commands -> request dispatched."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "correction_id": "corr-123",
                "outcome": "acknowledged",
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "dec-1", "correction_type": "amend", "reason": "test"},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "ok"
        assert len(daemon.requests) == 1
        assert daemon.requests[0]["command"]["name"] == "correction.request"

    @pytest.mark.asyncio
    async def test_lookup_gating_checks_before_dispatch(self, monkeypatch):
        """Capability check must happen before any tool_request is sent."""
        daemon = _NoLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool("bicameral.lookup", {"files": ["a.py"]})
        # No request should have been dispatched
        assert daemon.requests == []


# ---------------------------------------------------------------------------
# AC-2: Render daemon unavailable, unsupported, and deferred states explicitly
# ---------------------------------------------------------------------------


class TestExplicitStateRendering:
    """AC-2: Daemon states are rendered explicitly, never hidden."""

    @pytest.mark.asyncio
    async def test_daemon_unavailable_lookup(self, monkeypatch):
        """Unreachable daemon -> daemon_unavailable error for lookup."""
        monkeypatch.setattr(server, "_client", lambda: _UnreachableDaemon())

        content = await server.call_tool("bicameral.lookup", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_unavailable"
        assert "recovery" in response
        assert response["recovery"]["retryable"] is True

    @pytest.mark.asyncio
    async def test_daemon_unavailable_correction(self, monkeypatch):
        """Unreachable daemon -> daemon_unavailable error for correction."""
        monkeypatch.setattr(server, "_client", lambda: _UnreachableDaemon())

        content = await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "dec-1", "correction_type": "amend", "reason": "test"},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_unavailable"
        assert response["recovery"]["retryable"] is True

    @pytest.mark.asyncio
    async def test_unsupported_lookup_renders_recovery(self, monkeypatch):
        """Unsupported lookup.query -> recovery payload with tool/command."""
        daemon = _NoLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.lookup", {"files": ["a.py"]})
        response = json.loads(content[0].text)
        recovery = response["recovery"]

        assert recovery["error_code"] == "daemon_capability_error"
        assert recovery["category"] == "capability"
        assert recovery["requested_tool"] == "bicameral.lookup"
        assert recovery["requested_command"] == "lookup.query"

    @pytest.mark.asyncio
    async def test_unsupported_correction_renders_recovery(self, monkeypatch):
        """Unsupported correction.request -> recovery payload with details."""
        daemon = _NoLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "dec-1", "correction_type": "amend", "reason": "test"},
        )
        response = json.loads(content[0].text)
        recovery = response["recovery"]

        assert recovery["error_code"] == "daemon_capability_error"
        assert recovery["requested_tool"] == "bicameral.request_correction"
        assert recovery["requested_command"] == "correction.request"

    @pytest.mark.asyncio
    async def test_deferred_lookup_renders_capability_error(self, monkeypatch):
        """Daemon advertises command but defers at runtime -> capability error."""
        daemon = _DeferredLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.lookup", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_capability_error"
        assert "recovery" in response

    @pytest.mark.asyncio
    async def test_deferred_correction_renders_capability_error(self, monkeypatch):
        """Daemon advertises command but defers at runtime -> capability error."""
        daemon = _DeferredLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "dec-1", "correction_type": "amend", "reason": "fix"},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_capability_error"

    @pytest.mark.asyncio
    async def test_unavailable_recovery_includes_operator_action(self, monkeypatch):
        """Recovery payload for unavailable daemon includes actionable guidance."""
        monkeypatch.setattr(server, "_client", lambda: _UnreachableDaemon())

        content = await server.call_tool("bicameral.lookup", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert "operator_action" in response["recovery"]
        assert "Start or install" in response["recovery"]["operator_action"]


# ---------------------------------------------------------------------------
# AC-3: Conformance — MCP maps lookup/correction to bot ToolRequests
# ---------------------------------------------------------------------------


class TestToolRequestMapping:
    """AC-3: MCP correctly maps lookup/correction to ToolRequest envelopes."""

    def test_lookup_command_mapping(self):
        """bicameral.lookup maps to lookup.query bot command."""
        assert MCP_TOOL_COMMANDS["bicameral.lookup"] == "lookup.query"

    def test_correction_command_mapping(self):
        """bicameral.request_correction maps to correction.request bot command."""
        assert MCP_TOOL_COMMANDS["bicameral.request_correction"] == "correction.request"

    def test_lookup_in_supported_commands(self):
        """lookup.query appears in SUPPORTED_COMMANDS tuple."""
        assert "lookup.query" in SUPPORTED_COMMANDS

    def test_correction_in_supported_commands(self):
        """correction.request appears in SUPPORTED_COMMANDS tuple."""
        assert "correction.request" in SUPPORTED_COMMANDS

    @pytest.mark.asyncio
    async def test_lookup_toolrequest_envelope_shape(self, monkeypatch):
        """ToolRequest for lookup has correct command.name and params."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "recall_packet": {
                    "searched_sources": [],
                    "corpus_version": None,
                    "matches": [],
                    "unknown_scope": [],
                    "allowed_next_actions": [],
                },
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool(
            "bicameral.lookup",
            {"files": ["src/app.py"], "symbols": ["MyClass"], "scope": "pre_work"},
        )

        assert len(daemon.requests) == 1
        req = daemon.requests[0]
        assert req["command"]["name"] == "lookup.query"
        assert req["command"]["params"]["files"] == ["src/app.py"]
        assert req["command"]["params"]["symbols"] == ["MyClass"]
        assert req["command"]["params"]["scope"] == "pre_work"
        assert "request_id" in req
        assert "authority" in req
        assert "issued_at" in req

    @pytest.mark.asyncio
    async def test_correction_toolrequest_envelope_shape(self, monkeypatch):
        """ToolRequest for correction has correct command.name and params."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "correction_id": "corr-456",
                "outcome": "acknowledged",
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool(
            "bicameral.request_correction",
            {
                "target_id": "dec-99",
                "correction_type": "supersede",
                "reason": "outdated constraint",
                "context": {"source": "session"},
            },
        )

        assert len(daemon.requests) == 1
        req = daemon.requests[0]
        assert req["command"]["name"] == "correction.request"
        assert req["command"]["params"]["target_id"] == "dec-99"
        assert req["command"]["params"]["correction_type"] == "supersede"
        assert req["command"]["params"]["reason"] == "outdated constraint"
        assert req["command"]["params"]["context"] == {"source": "session"}
        assert "request_id" in req
        assert "authority" in req

    @pytest.mark.asyncio
    async def test_lookup_strips_control_params(self, monkeypatch):
        """Control params (actor_id, session_id, etc.) are stripped from command params."""
        daemon = _FullCapabilityDaemon(response_override={"status": "ok", "recall_packet": {}})
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool(
            "bicameral.lookup",
            {
                "files": ["a.py"],
                "actor_id": "user-1",
                "session_id": "sess-1",
                "workspace": "/repo",
            },
        )

        params = daemon.requests[0]["command"]["params"]
        assert "actor_id" not in params
        assert "session_id" not in params
        assert "workspace" not in params
        assert params["files"] == ["a.py"]

    @pytest.mark.asyncio
    async def test_correction_strips_control_params(self, monkeypatch):
        """Control params are stripped from correction command params."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "correction_id": "corr-1",
                "outcome": "acknowledged",
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool(
            "bicameral.request_correction",
            {
                "target_id": "dec-1",
                "correction_type": "withdraw",
                "reason": "no longer relevant",
                "actor_id": "admin",
                "policy_scope": ["prod"],
            },
        )

        params = daemon.requests[0]["command"]["params"]
        assert "actor_id" not in params
        assert "policy_scope" not in params
        assert params["target_id"] == "dec-1"

    def test_build_tool_request_lookup_structure(self):
        """build_tool_request produces valid envelope for lookup.query."""
        req = build_tool_request(
            command_name="lookup.query",
            params={"files": ["x.py"], "scope": "pre_work"},
            authority={"actor_id": "test", "auth_method": "mcp_session"},
        )
        assert req["command"]["name"] == "lookup.query"
        assert req["command"]["params"] == {"files": ["x.py"], "scope": "pre_work"}
        assert "request_id" in req
        assert "issued_at" in req

    def test_build_tool_request_correction_structure(self):
        """build_tool_request produces valid envelope for correction.request."""
        req = build_tool_request(
            command_name="correction.request",
            params={"target_id": "d-1", "correction_type": "amend", "reason": "fix"},
            authority={"actor_id": "test", "auth_method": "mcp_session"},
        )
        assert req["command"]["name"] == "correction.request"
        assert req["command"]["params"] == {
            "target_id": "d-1",
            "correction_type": "amend",
            "reason": "fix",
        }


# ---------------------------------------------------------------------------
# AC-4: No-local-state — MCP creates no SourceSnapshot, binding, or
#        compliance artifact
# ---------------------------------------------------------------------------


class TestNoLocalState:
    """AC-4: MCP creates no local SourceSnapshot, binding, or compliance artifact."""

    @pytest.mark.asyncio
    async def test_lookup_creates_no_local_files(self, monkeypatch, tmp_path):
        """Calling lookup must not create any files in workspace or tmp."""
        daemon = _FullCapabilityDaemon(
            response_override={"status": "ok", "recall_packet": {"matches": []}}
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        before = set(tmp_path.rglob("*"))
        await server.call_tool("bicameral.lookup", {"files": ["a.py"]})
        after = set(tmp_path.rglob("*"))

        assert before == after, f"lookup created local files: {after - before}"

    @pytest.mark.asyncio
    async def test_correction_creates_no_local_files(self, monkeypatch, tmp_path):
        """Calling correction must not create any files in workspace or tmp."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "correction_id": "c-1",
                "outcome": "acknowledged",
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        before = set(tmp_path.rglob("*"))
        await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "d-1", "correction_type": "amend", "reason": "fix"},
        )
        after = set(tmp_path.rglob("*"))

        assert before == after, f"correction created local files: {after - before}"

    @pytest.mark.asyncio
    async def test_lookup_does_not_create_source_snapshot(self, monkeypatch, tmp_path):
        """No SourceSnapshot file is written during lookup."""
        daemon = _FullCapabilityDaemon(response_override={"status": "ok", "recall_packet": {}})
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        await server.call_tool("bicameral.lookup", {"files": ["main.rs"]})

        snapshot_files = list(tmp_path.rglob("*snapshot*")) + list(
            tmp_path.rglob("*SourceSnapshot*")
        )
        assert snapshot_files == []

    @pytest.mark.asyncio
    async def test_lookup_does_not_create_binding(self, monkeypatch, tmp_path):
        """No binding artifact is created during lookup."""
        daemon = _FullCapabilityDaemon(response_override={"status": "ok", "recall_packet": {}})
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        await server.call_tool("bicameral.lookup", {"files": ["a.py"]})

        binding_files = list(tmp_path.rglob("*binding*"))
        assert binding_files == []

    @pytest.mark.asyncio
    async def test_lookup_does_not_create_compliance_artifact(self, monkeypatch, tmp_path):
        """No compliance artifact is created during lookup."""
        daemon = _FullCapabilityDaemon(response_override={"status": "ok", "recall_packet": {}})
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        await server.call_tool("bicameral.lookup", {"files": ["a.py"]})

        compliance_files = list(tmp_path.rglob("*compliance*"))
        assert compliance_files == []

    @pytest.mark.asyncio
    async def test_correction_does_not_create_source_snapshot(self, monkeypatch, tmp_path):
        """No SourceSnapshot file is written during correction."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "correction_id": "c-2",
                "outcome": "acknowledged",
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "d-1", "correction_type": "amend", "reason": "fix"},
        )

        snapshot_files = list(tmp_path.rglob("*snapshot*")) + list(
            tmp_path.rglob("*SourceSnapshot*")
        )
        assert snapshot_files == []

    @pytest.mark.asyncio
    async def test_correction_does_not_create_binding(self, monkeypatch, tmp_path):
        """No binding artifact is created during correction."""
        daemon = _FullCapabilityDaemon(
            response_override={
                "status": "ok",
                "correction_id": "c-3",
                "outcome": "acknowledged",
            }
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)
        monkeypatch.setenv("BICAMERAL_WORKSPACE", str(tmp_path))

        await server.call_tool(
            "bicameral.request_correction",
            {"target_id": "d-1", "correction_type": "withdraw", "reason": "obsolete"},
        )

        binding_files = list(tmp_path.rglob("*binding*"))
        assert binding_files == []

    def test_server_module_has_no_file_write_imports(self):
        """server.py does not import file-writing utilities."""
        import ast

        tree = ast.parse(Path("server.py").read_text())
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_names.add(node.module)

        forbidden_write_modules = {"shutil", "tempfile", "sqlite3"}
        overlap = imported_names & forbidden_write_modules
        assert not overlap, f"server.py imports file-write modules: {overlap}"

    def test_no_ledger_or_snapshot_module_exists(self):
        """Legacy local-state modules must not exist."""
        for name in ("ledger", "sources", "snapshots", "compliance"):
            assert not Path(name).exists(), f"local-state module present: {name}"
            assert not Path(f"{name}.py").exists(), f"local-state module present: {name}.py"
