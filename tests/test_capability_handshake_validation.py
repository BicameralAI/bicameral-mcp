"""Capability handshake validation (issue #584).

Proves the PROTOCOL_VERSION capability handshake between MCP and the
tagged daemon binary works correctly.  Each test class maps to one
acceptance criterion:

AC-1: Protocol match → CapabilityReport with daemon-advertised alpha
      commands, including preflight.run / bicameral.preflight.
AC-2: Protocol mismatch → fail-fast with structured recovery payload
      (#583).
AC-3: Unsupported/deferred commands → typed daemon capability errors.
AC-4: MCP does not start legacy local authority paths, infer compat
      locally, or fall back to old preflight/ledger/codegraph handlers.

Validation context:
  MCP commit:   caf106d (dev tip at run time)
  MCP version:  0.17.0
  Protocol:     v2 (TOOLREQUEST_PROTOCOL_VERSION)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

import server
from daemon_client import (
    CapabilityReport,
    DaemonCapabilityError,
    DaemonConnectionError,
    DaemonProtocolError,
)
from responses import build_recovery_payload
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

# Alpha commands that must appear in the daemon capability surface.
ALPHA_COMMANDS = SUPPORTED_COMMANDS


# ---------------------------------------------------------------------------
# Fake daemon clients
# ---------------------------------------------------------------------------


class _MatchingDaemon:
    """Daemon that returns a matching protocol version and full alpha surface."""

    def __init__(
        self,
        *,
        commands: tuple[str, ...] = ALPHA_COMMANDS,
        extra_fields: dict[str, Any] | None = None,
    ):
        self.commands = commands
        self.extra = extra_fields or {}
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(self.commands),
        }
        caps.update(self.extra)
        return caps

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {"echo_command": tool_request["command"]["name"]},
            "responded_at": "2026-06-16T00:00:00Z",
        }


class _MismatchedDaemon:
    """Daemon that returns an incompatible protocol version."""

    def __init__(self, *, protocol_version: str = "v1"):
        self.protocol_version = protocol_version
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": ["history.list"],
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {"status": "ok"}


class _UnsupportedCommandDaemon:
    """Daemon that matches protocol but rejects specific commands."""

    def __init__(self, *, deferred: set[str] | None = None):
        self.deferred = deferred or set()
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(ALPHA_COMMANDS),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        cmd = tool_request["command"]["name"]
        if cmd in self.deferred:
            raise DaemonCapabilityError(f"unsupported_command: daemon does not implement {cmd}")
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "result": {},
            "responded_at": "2026-06-16T00:00:00Z",
        }


# ---------------------------------------------------------------------------
# AC-1: Protocol match → CapabilityReport with alpha commands
# ---------------------------------------------------------------------------


class TestProtocolMatchCapabilityReport:
    """AC-1: Protocol match returns CapabilityReport with daemon-advertised
    alpha commands including preflight.run/bicameral.preflight."""

    @pytest.mark.asyncio
    async def test_protocol_match_returns_capability_report(self, monkeypatch):
        """On protocol match _ensure_protocol_compatible returns a
        CapabilityReport, not None."""
        daemon = _MatchingDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        report = await server._ensure_protocol_compatible(daemon)
        assert isinstance(report, CapabilityReport)

    @pytest.mark.asyncio
    async def test_capability_report_contains_protocol_versions(self, monkeypatch):
        daemon = _MatchingDaemon()
        report = await server._ensure_protocol_compatible(daemon)

        assert report.daemon_protocol_version == TOOLREQUEST_PROTOCOL_VERSION
        assert report.mcp_protocol_version == TOOLREQUEST_PROTOCOL_VERSION

    @pytest.mark.asyncio
    async def test_capability_report_contains_all_alpha_commands(self, monkeypatch):
        """The report lists every daemon-advertised alpha command."""
        daemon = _MatchingDaemon()
        report = await server._ensure_protocol_compatible(daemon)

        for cmd in ALPHA_COMMANDS:
            assert cmd in report.supported_commands, (
                f"daemon-advertised command {cmd!r} missing from CapabilityReport"
            )

    @pytest.mark.asyncio
    async def test_preflight_run_in_capability_report(self, monkeypatch):
        """preflight.run is explicitly present in daemon-advertised commands."""
        daemon = _MatchingDaemon()
        report = await server._ensure_protocol_compatible(daemon)

        assert "preflight.run" in report.supported_commands

    @pytest.mark.asyncio
    async def test_bicameral_preflight_maps_to_preflight_run(self):
        """bicameral.preflight MCP tool maps to preflight.run bot command."""
        assert MCP_TOOL_COMMANDS["bicameral.preflight"] == "preflight.run"

    @pytest.mark.asyncio
    async def test_capability_report_includes_daemon_endpoint(self, monkeypatch):
        daemon = _MatchingDaemon()
        report = await server._ensure_protocol_compatible(daemon)

        assert report.daemon_endpoint  # non-empty

    @pytest.mark.asyncio
    async def test_list_tools_succeeds_on_protocol_match(self, monkeypatch):
        """list_tools completes without error when protocol matches."""
        monkeypatch.setattr(server, "_client", lambda: _MatchingDaemon())
        tools = await server.list_tools()
        names = {t.name for t in tools}
        assert "bicameral.preflight" in names

    @pytest.mark.asyncio
    async def test_call_tool_succeeds_on_protocol_match(self, monkeypatch):
        """call_tool completes without error when protocol matches."""
        monkeypatch.setattr(server, "_client", lambda: _MatchingDaemon())
        content = await server.call_tool("bicameral.history", {})
        response = json.loads(content[0].text)
        assert response["status"] == "ok"

    @pytest.mark.asyncio
    async def test_capability_report_reflects_subset_daemon_surface(self, monkeypatch):
        """If daemon advertises fewer commands, report reflects that subset."""
        subset = ("preflight.run", "history.list")
        daemon = _MatchingDaemon(commands=subset)
        report = await server._ensure_protocol_compatible(daemon)

        assert set(report.supported_commands) == set(subset)


# ---------------------------------------------------------------------------
# AC-2: Protocol mismatch → fail-fast with recovery payload (#583)
# ---------------------------------------------------------------------------


class TestProtocolMismatchRecoveryPayload:
    """AC-2: Protocol mismatch fails fast with structured recovery payload."""

    @pytest.mark.asyncio
    async def test_mismatch_raises_daemon_protocol_error(self):
        daemon = _MismatchedDaemon(protocol_version="v1")
        with pytest.raises(DaemonProtocolError):
            await server._ensure_protocol_compatible(daemon)

    @pytest.mark.asyncio
    async def test_mismatch_does_not_dispatch_tool_request(self, monkeypatch):
        daemon = _MismatchedDaemon(protocol_version="v1")
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        assert daemon.requests == []

    @pytest.mark.asyncio
    async def test_mismatch_returns_typed_error_with_recovery(self, monkeypatch):
        daemon = _MismatchedDaemon(protocol_version="v1")
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_protocol_mismatch"
        assert "recovery" in response

    @pytest.mark.asyncio
    async def test_recovery_payload_has_required_fields(self, monkeypatch):
        daemon = _MismatchedDaemon(protocol_version="v1")
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)
        recovery = response["recovery"]

        assert recovery["error_code"] == "daemon_protocol_mismatch"
        assert recovery["category"] == "setup"
        assert recovery["retryable"] is False
        assert recovery["mcp_protocol_version"] == TOOLREQUEST_PROTOCOL_VERSION
        assert "operator_action" in recovery
        assert "daemon_endpoint" in recovery

    @pytest.mark.asyncio
    async def test_recovery_payload_includes_requested_tool(self, monkeypatch):
        daemon = _MismatchedDaemon(protocol_version="v1")
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        recovery = json.loads(content[0].text)["recovery"]

        assert recovery["requested_tool"] == "bicameral.preflight"
        assert recovery["requested_command"] == "preflight.run"

    @pytest.mark.asyncio
    async def test_mismatch_no_staged_output(self, monkeypatch):
        """Mismatch must not produce staged output (no fallback rendering)."""
        daemon = _MismatchedDaemon(protocol_version="v0")
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert "stages" not in response

    def test_build_recovery_payload_standalone(self):
        """build_recovery_payload produces correct shape for mismatch."""
        recovery = build_recovery_payload(
            error_code="daemon_protocol_mismatch",
            requested_tool="bicameral.preflight",
            requested_command="preflight.run",
            details={"daemon_protocol_version": "v1"},
        )
        assert recovery["error_code"] == "daemon_protocol_mismatch"
        assert recovery["mcp_protocol_version"] == TOOLREQUEST_PROTOCOL_VERSION
        assert recovery["daemon_protocol_version"] == "v1"


# ---------------------------------------------------------------------------
# AC-3: Unsupported/deferred commands → typed daemon capability errors
# ---------------------------------------------------------------------------


class TestUnsupportedCommandTypedErrors:
    """AC-3: Unsupported/deferred commands remain typed daemon capability errors."""

    @pytest.mark.asyncio
    async def test_unsupported_command_returns_capability_error(self, monkeypatch):
        daemon = _UnsupportedCommandDaemon(deferred={"preflight.run"})
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_capability_error"

    @pytest.mark.asyncio
    async def test_unsupported_command_recovery_has_category_capability(self, monkeypatch):
        daemon = _UnsupportedCommandDaemon(deferred={"history.list"})
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.history", {})
        response = json.loads(content[0].text)
        recovery = response["recovery"]

        assert recovery["category"] == "capability"
        assert recovery["retryable"] is False

    @pytest.mark.asyncio
    async def test_unsupported_command_no_staged_or_result(self, monkeypatch):
        """Error response must not contain staged output or a result key."""
        daemon = _UnsupportedCommandDaemon(deferred={"preflight.run"})
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert "stages" not in response
        assert "result" not in response

    @pytest.mark.asyncio
    async def test_unknown_mcp_tool_name_returns_unsupported_tool(self, monkeypatch):
        monkeypatch.setattr(server, "_client", lambda: _MatchingDaemon())
        content = await server.call_tool("bicameral.retired_legacy", {})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "unsupported_tool"

    @pytest.mark.asyncio
    async def test_daemon_connection_error_returns_unavailable(self, monkeypatch):
        """Connection failure → daemon_unavailable typed error."""

        class _UnreachableDaemon:
            async def capabilities(self) -> dict[str, Any]:
                raise DaemonConnectionError(
                    "cannot reach daemon", daemon_endpoint="http://127.0.0.1:37373"
                )

        monkeypatch.setattr(server, "_client", lambda: _UnreachableDaemon())
        content = await server.call_tool("bicameral.history", {})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_unavailable"
        assert response["recovery"]["retryable"] is True


# ---------------------------------------------------------------------------
# AC-4: No legacy local authority paths or fallback
# ---------------------------------------------------------------------------


class TestNoLegacyFallback:
    """AC-4: MCP does not start legacy local authority paths, infer
    compatibility locally, or fall back to old handlers."""

    def test_server_imports_no_legacy_modules(self):
        """server.py must not import legacy authority modules."""
        tree = ast.parse(Path("server.py").read_text())
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        legacy = {
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
        overlap = imported_roots & legacy
        assert not overlap, f"server.py imports legacy modules: {overlap}"

    def test_legacy_handler_directories_do_not_exist(self):
        """Deleted legacy directories must stay deleted."""
        for name in ("handlers", "ledger", "adapters", "codegenome", "sources"):
            assert not Path(name).exists(), f"legacy directory still present: {name}"

    @pytest.mark.asyncio
    async def test_mismatch_never_falls_back_to_local_preflight(self, monkeypatch):
        """On protocol mismatch, no local preflight execution occurs."""
        daemon = _MismatchedDaemon(protocol_version="v999")
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": ["src/main.rs"]})
        response = json.loads(content[0].text)

        assert response["error_code"] == "daemon_protocol_mismatch"
        assert "stages" not in response
        assert daemon.requests == []

    @pytest.mark.asyncio
    async def test_capability_error_never_falls_back_to_local_handler(self, monkeypatch):
        """On daemon capability error, no local handler is attempted."""
        daemon = _UnsupportedCommandDaemon(deferred={"ingest.submit_local"})
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.ingest",
            {"source_uri": "f", "source_type": "t", "title": "t", "description": "d"},
        )
        response = json.loads(content[0].text)

        assert response["error_code"] == "daemon_capability_error"
        assert "result" not in response

    def test_mcp_tool_commands_maps_only_to_bot_commands(self):
        """Every MCP tool maps to a daemon bot command, not a local handler."""
        for mcp_name, bot_cmd in MCP_TOOL_COMMANDS.items():
            assert "." in bot_cmd, (
                f"{mcp_name} maps to {bot_cmd!r} which doesn't look like a bot command"
            )
