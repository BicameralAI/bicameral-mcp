"""Conformance tests: staged preflight MCP bridge (issue #587).

Verifies the MCP thin client consumes the staged daemon response from
bot#323 and preserves the no-authority-fallback boundary. Each test maps
to one or more acceptance criteria from the issue:

AC-1: MCP test fixture covers staged response with
      capture/projection/lookup/enforcement/session_directive.
AC-2: MCP renders not_configured and unsupported stages explicitly.
AC-3: MCP follows session_directive.mode only as returned by daemon.
AC-4: MCP does not transform lookup.status=completed into
      safe-to-proceed/no-conflict language.
AC-5: MCP does not transform enforcement.status=not_configured into
      warning, pause, or block.
AC-6: Capability mismatch or unsupported daemon command fails typed and
      does not use legacy fallback.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import server
from daemon_client import DaemonCapabilityError, DaemonProtocolError
from responses import format_preflight_response
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

# ---------------------------------------------------------------------------
# Full staged preflight fixture (bot#323 contract)
# ---------------------------------------------------------------------------

CONFORMANCE_STAGED_FIXTURE: dict[str, Any] = {
    "capture": {"status": "active", "source": "transcript-001"},
    "projection": {"status": "active", "projected_decisions": ["DEC-42"]},
    "lookup": {
        "status": "completed",
        "decision_refs": [
            "a0000001-0000-4000-8000-000000000001",
            "a0000001-0000-4000-8000-000000000002",
        ],
        "limitations": [
            "graph: Graph data unavailable. Using source-only preflight.",
        ],
    },
    "enforcement": {
        "status": "not_configured",
    },
    "session_directive": {"mode": "continue"},
}


def _daemon_response(staged: dict[str, Any] | None) -> dict[str, Any]:
    """Build a minimal daemon preflight.run response."""
    response: dict[str, Any] = {
        "request_id": "req-conformance-001",
        "status": "ok",
        "relevant_decisions": [],
        "relevant_candidates": [],
        "readiness": {"unknown_scope": []},
        "warnings": [],
        "responded_at": "2026-06-14T00:00:00Z",
    }
    if staged is not None:
        response["staged"] = staged
    return response


class _FakeDaemonClient:
    """Fake daemon client for conformance tests."""

    def __init__(
        self,
        *,
        protocol_version: str = TOOLREQUEST_PROTOCOL_VERSION,
        staged: dict[str, Any] | None = None,
        unsupported_command: bool = False,
    ):
        self.protocol_version = protocol_version
        self.staged = staged if staged is not None else CONFORMANCE_STAGED_FIXTURE
        self.unsupported_command = unsupported_command
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": self.protocol_version,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        if self.unsupported_command:
            raise DaemonCapabilityError(
                "unsupported_command: daemon does not implement this command"
            )
        base: dict[str, Any] = {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "relevant_decisions": [],
            "relevant_candidates": [],
            "readiness": {"unknown_scope": []},
            "warnings": [],
            "responded_at": "2026-06-14T00:00:00Z",
        }
        if tool_request["command"]["name"] == "preflight.run":
            base["staged"] = self.staged
        return base


# ---------------------------------------------------------------------------
# AC-1: Fixture covers all five staged sections
# ---------------------------------------------------------------------------


class TestStagedFixtureCoverage:
    """AC-1: MCP test fixture covers staged response with
    capture/projection/lookup/enforcement/session_directive."""

    def test_fixture_contains_all_required_stages(self):
        for stage in ("capture", "projection", "lookup", "enforcement"):
            assert stage in CONFORMANCE_STAGED_FIXTURE, f"fixture missing required stage: {stage}"

    def test_fixture_contains_session_directive(self):
        assert "session_directive" in CONFORMANCE_STAGED_FIXTURE
        assert "mode" in CONFORMANCE_STAGED_FIXTURE["session_directive"]

    def test_lookup_has_decision_refs_and_limitations(self):
        lookup = CONFORMANCE_STAGED_FIXTURE["lookup"]
        assert "decision_refs" in lookup
        assert "limitations" in lookup
        assert len(lookup["decision_refs"]) > 0

    def test_render_produces_all_stages_in_output(self):
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert "stages" in output
        for stage in ("capture", "projection", "lookup", "enforcement"):
            assert stage in output["stages"], f"output missing stage: {stage}"

    def test_render_produces_session_directive_in_output(self):
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert "session_directive" in output
        assert output["session_directive"] == {"mode": "continue"}


# ---------------------------------------------------------------------------
# AC-2: Renders not_configured and unsupported stages explicitly
# ---------------------------------------------------------------------------


class TestExplicitStageRendering:
    """AC-2: MCP renders not_configured and unsupported stages explicitly."""

    def test_not_configured_rendered_literally(self):
        """enforcement.status=not_configured is preserved verbatim in output."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["stages"]["enforcement"]["status"] == "not_configured"

    def test_missing_stage_rendered_as_unsupported(self):
        """Stages absent from daemon response become status=unsupported."""
        partial = {
            "lookup": {"status": "completed", "decision_refs": [], "limitations": []},
            "session_directive": {"mode": "continue"},
        }
        daemon_resp = _daemon_response(partial)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["stages"]["capture"]["status"] == "unsupported"
        assert output["stages"]["projection"]["status"] == "unsupported"
        assert output["stages"]["enforcement"]["status"] == "unsupported"

    def test_no_staged_key_renders_all_unsupported(self):
        """When daemon omits staged entirely, all stages are unsupported."""
        daemon_resp = _daemon_response(staged=None)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        for stage in ("capture", "projection", "lookup", "enforcement"):
            assert output["stages"][stage] == {"status": "unsupported"}

    def test_not_configured_capture_rendered_verbatim(self):
        """capture.status=not_configured is preserved, not elided or renamed."""
        staged = {
            **CONFORMANCE_STAGED_FIXTURE,
            "capture": {"status": "not_configured"},
        }
        daemon_resp = _daemon_response(staged)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["stages"]["capture"]["status"] == "not_configured"


# ---------------------------------------------------------------------------
# AC-3: MCP follows session_directive.mode only as returned by daemon
# ---------------------------------------------------------------------------


class TestSessionDirectivePassthrough:
    """AC-3: MCP follows session_directive.mode only as returned by daemon."""

    @pytest.mark.parametrize("mode", ["continue", "warn", "pause", "block"])
    def test_session_directive_mode_forwarded_verbatim(self, mode: str):
        """session_directive.mode is forwarded exactly as daemon returns it."""
        staged = {
            **CONFORMANCE_STAGED_FIXTURE,
            "session_directive": {"mode": mode},
        }
        daemon_resp = _daemon_response(staged)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["session_directive"]["mode"] == mode

    def test_session_directive_extra_fields_forwarded(self):
        """Extra daemon fields on session_directive are not stripped."""
        staged = {
            **CONFORMANCE_STAGED_FIXTURE,
            "session_directive": {"mode": "warn", "reason": "stale graph"},
        }
        daemon_resp = _daemon_response(staged)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["session_directive"]["mode"] == "warn"
        assert output["session_directive"]["reason"] == "stale graph"

    def test_mcp_does_not_synthesize_session_directive_mode(self):
        """MCP never invents a mode — if daemon says continue, MCP says continue.

        Regression guard: MCP must not infer 'warn' or 'block' from stage data.
        """
        # enforcement=not_configured + lookup=completed: MCP must not escalate
        staged = {
            "capture": {"status": "not_configured"},
            "projection": {"status": "not_configured"},
            "lookup": {"status": "completed", "decision_refs": ["d1"], "limitations": []},
            "enforcement": {"status": "not_configured"},
            "session_directive": {"mode": "continue"},
        }
        daemon_resp = _daemon_response(staged)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["session_directive"]["mode"] == "continue"


# ---------------------------------------------------------------------------
# AC-4: MCP does not transform lookup.status=completed
# ---------------------------------------------------------------------------


class TestLookupCompletedNoTransformation:
    """AC-4: MCP does not transform lookup.status=completed into
    safe-to-proceed/no-conflict language."""

    def test_lookup_completed_status_preserved_verbatim(self):
        """lookup.status stays 'completed', not rewritten to 'safe' or 'clear'."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        lookup = output["stages"]["lookup"]
        assert lookup["status"] == "completed"

    def test_lookup_output_contains_no_safe_language(self):
        """The lookup stage output must not introduce MCP-owned safety claims."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        text = content.text.lower()

        forbidden_phrases = [
            "safe to proceed",
            "safe-to-proceed",
            "no conflict",
            "no-conflict",
            "all clear",
            "no issues found",
            "proceed safely",
        ]
        for phrase in forbidden_phrases:
            assert phrase not in text, (
                f"MCP injected forbidden language '{phrase}' into lookup output"
            )

    def test_lookup_decision_refs_forwarded_unmodified(self):
        """Decision refs from daemon are forwarded without filtering or renaming."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        lookup = output["stages"]["lookup"]
        assert lookup["decision_refs"] == CONFORMANCE_STAGED_FIXTURE["lookup"]["decision_refs"]

    def test_lookup_limitations_forwarded_unmodified(self):
        """Limitations from daemon are forwarded without downgrading or removing."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        lookup = output["stages"]["lookup"]
        assert lookup["limitations"] == CONFORMANCE_STAGED_FIXTURE["lookup"]["limitations"]


# ---------------------------------------------------------------------------
# AC-5: MCP does not transform enforcement.status=not_configured
# ---------------------------------------------------------------------------


class TestEnforcementNotConfiguredBoundary:
    """AC-5: MCP does not transform enforcement.status=not_configured into
    warning, pause, or block."""

    def test_enforcement_not_configured_stays_not_configured(self):
        """enforcement.status remains 'not_configured' verbatim."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        assert output["stages"]["enforcement"]["status"] == "not_configured"

    def test_enforcement_not_configured_behavior_is_none(self):
        """enforcement.behavior is 'none' — MCP never escalates to blocking."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        enforcement = output["stages"]["enforcement"]
        assert enforcement["behavior"] == "none"

    def test_enforcement_not_configured_no_blocking_language(self):
        """Output must not contain warning/pause/block/halt language for
        an enforcement stage that is not_configured."""
        daemon_resp = _daemon_response(CONFORMANCE_STAGED_FIXTURE)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        enforcement = output["stages"]["enforcement"]
        enforcement_text = json.dumps(enforcement).lower()

        forbidden = ["warning", "pause", "block", "halt", "reject", "deny"]
        for word in forbidden:
            assert word not in enforcement_text, (
                f"enforcement with status=not_configured must not contain '{word}'"
            )

    def test_enforcement_active_status_not_escalated_by_mcp(self):
        """When daemon returns enforcement.status=active, MCP preserves it
        without adding behavior=block or behavior=warn on its own."""
        staged = {
            **CONFORMANCE_STAGED_FIXTURE,
            "enforcement": {"status": "active", "policy": "advisory"},
        }
        daemon_resp = _daemon_response(staged)
        content = format_preflight_response(daemon_resp)
        output = json.loads(content.text)

        enforcement = output["stages"]["enforcement"]
        assert enforcement["status"] == "active"
        assert enforcement["policy"] == "advisory"
        # MCP must not inject behavior field when daemon doesn't provide one
        # (behavior=none is only injected for not_configured)
        assert enforcement.get("behavior") is None


# ---------------------------------------------------------------------------
# AC-6: Capability mismatch / unsupported command fails typed
# ---------------------------------------------------------------------------


class TestTypedFailureNoLegacyFallback:
    """AC-6: Capability mismatch or unsupported daemon command fails typed
    and does not use legacy fallback."""

    @pytest.mark.asyncio
    async def test_protocol_version_mismatch_returns_typed_error(self, monkeypatch):
        """Protocol mismatch produces a typed error, not a silent fallback."""
        fake = _FakeDaemonClient(protocol_version="v999")
        monkeypatch.setattr(server, "_client", lambda: fake)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_protocol_mismatch"
        # Must not have stages — means no fallback rendering happened
        assert "stages" not in response

    @pytest.mark.asyncio
    async def test_protocol_mismatch_does_not_dispatch_request(self, monkeypatch):
        """On protocol mismatch, no ToolRequest is ever sent to the daemon."""
        fake = _FakeDaemonClient(protocol_version="v0-incompatible")
        monkeypatch.setattr(server, "_client", lambda: fake)

        await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        assert fake.requests == []

    @pytest.mark.asyncio
    async def test_unsupported_command_returns_typed_error(self, monkeypatch):
        """Unsupported daemon command raises DaemonCapabilityError → typed error."""
        fake = _FakeDaemonClient(unsupported_command=True)
        monkeypatch.setattr(server, "_client", lambda: fake)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "daemon_capability_error"

    @pytest.mark.asyncio
    async def test_unsupported_command_no_legacy_fallback(self, monkeypatch):
        """On DaemonCapabilityError, no legacy code path is attempted."""
        fake = _FakeDaemonClient(unsupported_command=True)
        monkeypatch.setattr(server, "_client", lambda: fake)

        content = await server.call_tool("bicameral.preflight", {"files": ["a.py"]})
        response = json.loads(content[0].text)

        # Must not contain staged output (legacy fallback would produce this)
        assert "stages" not in response
        # Must not contain result key (only error payload)
        assert "result" not in response

    @pytest.mark.asyncio
    async def test_unsupported_tool_name_returns_typed_error(self, monkeypatch):
        """Calling a tool name not in MCP_TOOL_COMMANDS returns typed error."""
        fake = _FakeDaemonClient()
        monkeypatch.setattr(server, "_client", lambda: fake)

        content = await server.call_tool("bicameral.retired_legacy_tool", {})
        response = json.loads(content[0].text)

        assert response["status"] == "error"
        assert response["error_code"] == "unsupported_tool"

    @pytest.mark.asyncio
    async def test_no_legacy_module_imports_in_server(self):
        """Server must not import legacy authority modules (boundary check)."""
        import ast
        from pathlib import Path

        tree = ast.parse(Path("server.py").read_text())
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])

        legacy_modules = {
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
        overlap = imported_roots & legacy_modules
        assert not overlap, f"server.py imports legacy modules: {overlap}"


# ---------------------------------------------------------------------------
# Integration: end-to-end through server.call_tool
# ---------------------------------------------------------------------------


class TestEndToEndPreflightConformance:
    """Integration: full call_tool path verifying conformance end-to-end."""

    @pytest.mark.asyncio
    async def test_full_staged_response_through_call_tool(self, monkeypatch):
        """End-to-end: daemon staged response flows through call_tool unchanged."""
        fake = _FakeDaemonClient(staged=CONFORMANCE_STAGED_FIXTURE)
        monkeypatch.setattr(server, "_client", lambda: fake)
        monkeypatch.setenv("BICAMERAL_ACTOR_ID", "agent-conformance")
        monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")

        content = await server.call_tool(
            "bicameral.preflight",
            {"files": ["src/handler.py"], "symbols": ["MyClass"], "branch": "dev"},
        )
        output = json.loads(content[0].text)

        # All stages present
        assert set(output["stages"].keys()) == {
            "capture",
            "projection",
            "lookup",
            "enforcement",
        }
        # Stage data preserved from daemon
        assert output["stages"]["capture"]["status"] == "active"
        assert output["stages"]["projection"]["projected_decisions"] == ["DEC-42"]
        assert output["stages"]["lookup"]["status"] == "completed"
        assert output["stages"]["enforcement"]["status"] == "not_configured"
        assert output["stages"]["enforcement"]["behavior"] == "none"
        # session_directive passthrough
        assert output["session_directive"] == {"mode": "continue"}

    @pytest.mark.asyncio
    async def test_non_preflight_tool_no_staged_rendering(self, monkeypatch):
        """Non-preflight tools must not receive staged formatting."""
        fake = _FakeDaemonClient()
        monkeypatch.setattr(server, "_client", lambda: fake)

        content = await server.call_tool("bicameral.history", {})
        output = json.loads(content[0].text)

        assert "stages" not in output
        assert "session_directive" not in output
