"""Tagged-daemon handshake regression over the real HTTP transport (issue #584).

The companion suite ``test_capability_handshake_validation.py`` proves the
MCP-side handshake *logic* is correct, but every one of its daemons is an
in-process stub injected via ``monkeypatch.setattr(server, "_client", ...)``.
That never exercises:

* the real :class:`DaemonClient` urllib transport, or
* the surface a **released** ``bicameral-bot`` daemon binary actually serves.

This module closes that gap. It stands up a real ``http.server`` daemon on an
ephemeral loopback port and drives it through the production ``DaemonClient``.

The headline case reproduces the released daemon contract observed against the
tagged ``bicameral-bot v0.1.4`` binary: ``bicameral gateway start`` serves only
the ``/api/v1/*`` REST surface (plus ``/health``) and returns **404** for
``GET /v2/capabilities``. The handshake must therefore fail fast with a typed
``daemon_unavailable`` error and must not fall back to any local handler.

Validation context recorded for #584 / #585:
  MCP under test:   commit a5e15cc, version 0.17.0, protocol v2
  Tagged daemon:    bicameral-bot v0.1.4 (commit bc2dec6)
  Observed surface: GET /health -> 200 {"version":"0.1.4"};
                    GET /api/v1/status -> 200; GET /v2/capabilities -> 404
  Limitation:       no released bot tag exposes /v2/capabilities or
                    /v2/tool-requests, so the protocol-match path cannot be
                    validated end-to-end against a tagged daemon. Blocks #585.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

import server
from daemon_client import (
    DEFAULT_DAEMON_URL,
    CapabilityReport,
    DaemonCapabilityError,
    DaemonConnectionError,
    DaemonProtocolError,
)
from tool_request import SUPPORTED_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION


def _make_handler(routes: dict[tuple[str, str], tuple[int, dict[str, Any]]]):
    """Build a request handler serving ``routes`` keyed by ``(method, path)``.

    A missing route returns 404, mirroring how the released daemon responds to
    the unimplemented ``/v2`` surface.
    """

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # silence test noise
            pass

        def _respond(self) -> None:
            if self.command == "POST":
                length = int(self.headers.get("content-length", 0))
                self.rfile.read(length)
            status, body = routes.get((self.command, self.path), (404, {}))
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _respond
        do_POST = _respond

    return _Handler


@contextmanager
def _daemon(routes: dict[tuple[str, str], tuple[int, dict[str, Any]]]) -> Iterator[str]:
    """Run a loopback HTTP daemon serving ``routes``; yield its base URL."""
    httpd = HTTPServer(("127.0.0.1", 0), _make_handler(routes))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


# Released bicameral-bot v0.1.4 surface: /api/v1/* + /health only, no /v2.
_RELEASED_DAEMON_ROUTES: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {
    ("GET", "/health"): (200, {"status": "ok", "version": "0.1.4"}),
    ("GET", "/api/v1/status"): (200, {"status": "ok"}),
}

# Conformant daemon that implements the v2 ToolRequest handshake surface.
_CONFORMANT_CAPABILITIES = {
    "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
    "supported_commands": list(SUPPORTED_COMMANDS),
}


def _client_for(base_url: str):
    from daemon_client import DaemonClient

    return DaemonClient(base_url=base_url, timeout_seconds=5.0)


# ---------------------------------------------------------------------------
# Released tagged daemon: no /v2 surface -> fail fast, no fallback
# ---------------------------------------------------------------------------


class TestReleasedDaemonSurface:
    """Reproduces the tagged bicameral-bot v0.1.4 daemon surface over real HTTP."""

    async def test_capabilities_probe_returns_404_as_daemon_unavailable(self):
        with _daemon(_RELEASED_DAEMON_ROUTES) as base_url:
            client = _client_for(base_url)
            # Sanity: the daemon is reachable and healthy on /health...
            health = await client._json_request("GET", "/health")
            assert health == {"status": "ok", "version": "0.1.4"}
            # ...but the v2 capability surface the MCP requires is absent.
            with pytest.raises(DaemonConnectionError) as exc_info:
                await client.capabilities()
        assert exc_info.value.code == "daemon_unavailable"
        assert "/v2/capabilities" in str(exc_info.value)

    async def test_handshake_fails_fast_against_released_surface(self):
        with _daemon(_RELEASED_DAEMON_ROUTES) as base_url:
            client = _client_for(base_url)
            with pytest.raises(DaemonConnectionError) as exc_info:
                await server._ensure_protocol_compatible(client)
        assert exc_info.value.code == "daemon_unavailable"

    async def test_call_tool_emits_typed_recovery_and_no_local_fallback(self, monkeypatch):
        with _daemon(_RELEASED_DAEMON_ROUTES) as base_url:
            monkeypatch.setattr(server, "_client", lambda: _client_for(base_url))
            result = await server.call_tool("bicameral.preflight", {})
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert payload["status"] == "error"
        assert payload["error_code"] == "daemon_unavailable"
        assert payload["recovery"]["error_code"] == "daemon_unavailable"
        assert payload["recovery"]["requested_command"] == "preflight.run"
        # No legacy fallback: the daemon never produced a staged preflight or a
        # result, so neither may appear in the typed error envelope.
        assert "staged" not in payload
        assert "result" not in payload


# ---------------------------------------------------------------------------
# Conformant v2 daemon over real HTTP: the contract a tagged daemon must meet
# ---------------------------------------------------------------------------


class TestConformantDaemonOverHttp:
    """Positive control: a daemon that DOES serve /v2/capabilities."""

    async def test_protocol_match_returns_capability_report_over_http(self):
        routes = {("GET", "/v2/capabilities"): (200, _CONFORMANT_CAPABILITIES)}
        with _daemon(routes) as base_url:
            client = _client_for(base_url)
            report = await server._ensure_protocol_compatible(client)
        assert isinstance(report, CapabilityReport)
        assert report.daemon_protocol_version == TOOLREQUEST_PROTOCOL_VERSION
        assert "preflight.run" in report.supported_commands
        assert set(report.supported_commands) == set(SUPPORTED_COMMANDS)

    async def test_protocol_mismatch_over_http_fails_fast_with_recovery(self):
        routes = {
            ("GET", "/v2/capabilities"): (
                200,
                {"toolrequest_protocol_version": "v1", "supported_commands": []},
            )
        }
        with _daemon(routes) as base_url:
            client = _client_for(base_url)
            with pytest.raises(DaemonProtocolError) as exc_info:
                await server._ensure_protocol_compatible(client)
        assert exc_info.value.code == "daemon_protocol_mismatch"
        assert exc_info.value.details["daemon_protocol_version"] == "v1"

    async def test_unsupported_command_over_http_is_typed_capability_error(self):
        routes = {
            ("GET", "/v2/capabilities"): (200, _CONFORMANT_CAPABILITIES),
            ("POST", "/v2/tool-requests"): (
                200,
                {"status": "error", "message": "unsupported_command"},
            ),
        }
        with _daemon(routes) as base_url:
            client = _client_for(base_url)
            await server._ensure_protocol_compatible(client)
            with pytest.raises(DaemonCapabilityError) as exc_info:
                await client.send_tool_request(
                    {
                        "request_id": "r1",
                        "command": {"name": "review.resolve_compliance", "params": {}},
                        "authority": {},
                    }
                )
        assert exc_info.value.code == "daemon_capability_error"


def test_default_daemon_url_documents_required_port():
    """MCP expects the daemon on :37373; the released binary binds :7525.

    This constant assertion records the port half of the contract gap so a
    future daemon release that aligns the port (and adds the /v2 surface)
    flips the released-surface tests above from red-by-design to green.
    """
    assert DEFAULT_DAEMON_URL == "http://127.0.0.1:37373"
