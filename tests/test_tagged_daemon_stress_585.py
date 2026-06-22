"""Devin-through-MCP stress regression against the tagged daemon surface (issue #585).

The companion suite ``test_tagged_daemon_handshake_regression.py`` (#584) proved
the released ``bicameral-bot v0.1.4`` daemon served only ``/api/v1/*`` and 404'd
``/v2/capabilities``, so the Devin hand-off loop could not run end-to-end. Bot
``v0.1.5`` (tag ``161e174``, release PR bicameral-bot#513) ships the v2 ToolRequest
HTTP surface (#427/#430) on the MCP-aligned default port, unblocking #585.

This module locks in the **v0.1.5 contract** by standing up a loopback HTTP daemon
whose responses are modeled on the live transcript captured against the tagged
``bicameral-bot v0.1.5`` binary (``bicameral gateway start``), then driving it
through the production MCP code paths (``server.list_tools``,
``server._ensure_protocol_compatible``, ``server.call_tool`` ->
``DaemonClient`` urllib transport). It exercises the full stress matrix:

* handshake -> ``CapabilityReport`` (protocol ``v2``, 16 commands, deferred set);
* supported read command (``history.list``) through ``call_tool``;
* staged preflight rendering;
* deferred command (``review.resolve_compliance``) -> typed, daemon-authored
  ``rejected`` payload forwarded verbatim (MCP synthesizes nothing, no fallback);
* agent retry behavior (iterated identical calls stay consistent);
* concurrent tool calls;
* no MCP-local fallback for out-of-scope surfaces (graph/locator/dashboard/
  install/upgrade/migration/daemon lifecycle).

Live validation context recorded for #585 (see docs/validation-585-tagged-daemon-stress.md):
  MCP under test:   bicameral-mcp 0.17.0, protocol v2
  Tagged daemon:    bicameral-bot v0.1.5 (tag 161e174), built from source on a
                    glibc-2.35 host (published linux tarball needs GLIBC_2.39)
  GET /health        -> {"status":"ok","version":"0.1.5"}
  GET /v2/capabilities -> 200, protocol_version v2, 16 supported commands,
                          deferred [resolve_compliance, untrack_source, refresh_query]
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import server
from daemon_client import CapabilityReport, DaemonClient
from version import TOOLREQUEST_PROTOCOL_VERSION

# Observed v0.1.5 capability surface (live transcript).
_V015_SUPPORTED_COMMANDS = [
    "ingest.submit_local",
    "ingest.submit_managed",
    "history.list",
    "search.query",
    "review.accept_candidate",
    "review.reject_candidate",
    "review.approve_signoff",
    "review.reject_signoff",
    "binding.create",
    "binding.inspect",
    "evidence.refresh",
    "preflight.run",
    "code.locate",
    "graph.status",
    "graph.refresh_snapshot",
    "decision.find_code_impact",
]
_V015_DEFERRED_COMMANDS = [
    "review.resolve_compliance",
    "tracking.untrack_source",
    "tracking.refresh_query",
]
_V015_CAPABILITIES = {
    "protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
    "supported_commands": _V015_SUPPORTED_COMMANDS,
    "deferred_commands": _V015_DEFERRED_COMMANDS,
}


def _ok(command: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": "fixed-request-id",
        "status": "ok",
        "result": result,
        "responded_at": "2026-06-21T00:00:00Z",
        "trace": {"command": command, "caller_surface": "mcp", "status": "ok"},
    }


def _tool_response_for(command: str) -> dict[str, Any]:
    """Model the v0.1.5 daemon's ToolResponse for a given command."""
    if command == "history.list":
        return _ok(command, {"decisions": []})
    if command == "search.query":
        return _ok(command, {"results": [], "code_context": {"candidates": []}})
    if command == "preflight.run":
        # v0.1.5 returns an advisory result without a `staged` envelope.
        return _ok(command, {"readiness": {"state": "ready"}})
    if command == "review.resolve_compliance":
        # Deferred command: daemon authors a typed `rejected` payload (not an
        # `error`/`unsupported_command`), which MCP must forward verbatim.
        msg = (
            "unsupported_capability: review.resolve_compliance is deferred until "
            "V1 compliance enforcement, correctness assertion, and signoff "
            "decoupling are specified"
        )
        return {
            "request_id": "fixed-request-id",
            "status": "rejected",
            "message": msg,
            "responded_at": "2026-06-21T00:00:00Z",
            "trace": {
                "command": command,
                "caller_surface": "mcp",
                "status": "rejected",
                "failure_reason": msg,
            },
        }
    return _ok(command, {})


class _V015Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence test noise
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"status": "ok", "version": "0.1.5"})
        elif self.path == "/v2/capabilities":
            self._send(200, _V015_CAPABILITIES)
        else:
            self._send(404, {})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length)
        if self.path != "/v2/tool-requests":
            self._send(404, {})
            return
        request = json.loads(raw)
        command = request.get("command", {}).get("name", "")
        self._send(200, _tool_response_for(command))

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@contextmanager
def _v015_daemon() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _V015Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _client_for(base_url: str) -> DaemonClient:
    return DaemonClient(base_url=base_url, timeout_seconds=5.0)


class TestTaggedV015StressMatrix:
    """Devin-through-MCP stress matrix against the v0.1.5 daemon surface."""

    async def test_health_reports_v015(self):
        with _v015_daemon() as base_url:
            health = await _client_for(base_url)._json_request("GET", "/health")
        assert health == {"status": "ok", "version": "0.1.5"}

    async def test_handshake_returns_capability_report_protocol_v2(self):
        with _v015_daemon() as base_url:
            report = await server._ensure_protocol_compatible(_client_for(base_url))
        assert isinstance(report, CapabilityReport)
        assert report.daemon_protocol_version == "v2"
        assert "preflight.run" in report.supported_commands
        assert set(report.supported_commands) == set(_V015_SUPPORTED_COMMANDS)

    async def test_list_tools_after_handshake(self, monkeypatch):
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            tools = await server.list_tools()
        assert tools  # handshake succeeded and tool list returned

    async def test_supported_read_command_history_list_ok(self, monkeypatch):
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            result = await server.call_tool("bicameral.history", {})
        payload = json.loads(result[0].text)
        assert payload["status"] == "ok"
        assert payload["result"] == {"decisions": []}

    async def test_staged_preflight_rendered_without_warn_or_block(self, monkeypatch):
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            result = await server.call_tool("bicameral.preflight", {"files": ["src/lib.rs"]})
        payload = json.loads(result[0].text)
        assert payload["status"] == "ok"
        # v0.1.5 emits no `staged` envelope -> stages render as unsupported, and
        # not_configured/absent enforcement is never promoted to warn/pause/block.
        assert set(payload["stages"]) == {"capture", "projection", "lookup", "enforcement"}
        assert payload["stages"]["enforcement"]["status"] == "unsupported"
        assert payload["session_directive"] == {"mode": "continue"}

    async def test_deferred_command_returns_typed_daemon_authored_rejection(self, monkeypatch):
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            result = await server.call_tool(
                "bicameral.review.resolve_compliance",
                {"target_id": "dec-x", "compliance_verdict": "compliant"},
            )
        payload = json.loads(result[0].text)
        # Daemon authors the rejection; MCP forwards it verbatim, synthesizing
        # neither success nor a local handler result.
        assert payload["status"] == "rejected"
        assert "unsupported_capability" in payload["message"]
        assert payload["trace"]["failure_reason"].startswith("unsupported_capability")
        assert "staged" not in payload

    async def test_agent_retry_iterated_calls_are_consistent(self, monkeypatch):
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            statuses = []
            for _ in range(5):
                result = await server.call_tool("bicameral.history", {})
                statuses.append(json.loads(result[0].text)["status"])
        assert statuses == ["ok"] * 5

    async def test_concurrent_tool_calls(self, monkeypatch):
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            results = await asyncio.gather(
                server.call_tool("bicameral.history", {}),
                server.call_tool("bicameral.search", {"query": "a"}),
                server.call_tool("bicameral.preflight", {"files": ["x"]}),
                server.call_tool("bicameral.history", {}),
                server.call_tool("bicameral.search", {"query": "b"}),
            )
        statuses = [json.loads(r[0].text)["status"] for r in results]
        assert statuses == ["ok"] * 5

    async def test_no_local_fallback_for_out_of_scope_surfaces(self, monkeypatch):
        """Graph/locator/dashboard/install/upgrade/migration/lifecycle never
        resolve to an MCP handler; out-of-scope tool names return a typed
        ``unsupported_tool`` error without any daemon round-trip."""
        from tool_request import MCP_TOOL_COMMANDS

        forbidden = [
            "bicameral.graph.status",
            "bicameral.graph.refresh_snapshot",
            "bicameral.code.locate",
            "bicameral.dashboard",
            "bicameral.install",
            "bicameral.update",
            "bicameral.migrate",
            "bicameral.gateway.start",
            "bicameral.service",
            "bicameral.uninstall",
        ]
        assert not [name for name in forbidden if name in MCP_TOOL_COMMANDS]
        with _v015_daemon() as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            for name in forbidden:
                result = await server.call_tool(name, {})
                payload = json.loads(result[0].text)
                assert payload["error_code"] == "unsupported_tool"
