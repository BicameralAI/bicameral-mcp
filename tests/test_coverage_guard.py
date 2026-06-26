"""Tests for the preflight coverage guard (issue #343).

Proves:
1. Files with zero ledger/code_region coverage trigger a no-fire fast-path.
2. Files with any coverage proceed to full preflight.
3. The guard fails open on errors, partial coverage, or missing capabilities.
4. The no-fire response has the correct shape for agent consumption.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import server
from coverage_guard import check_coverage
from daemon_client import DaemonClientError, DaemonConnectionError
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

# Preflight staged response fixture for the full-fire path.
_STAGED_PREFLIGHT = {
    "capture": {"status": "not_configured"},
    "projection": {"status": "not_configured"},
    "lookup": {"status": "completed", "decision_refs": ["DEC-1"]},
    "enforcement": {"status": "not_configured"},
    "session_directive": {"mode": "continue"},
}


# ---------------------------------------------------------------------------
# Fake daemon clients
# ---------------------------------------------------------------------------


class _NoCoverageDaemon:
    """Daemon where all files are unknown (zero coverage)."""

    def __init__(self, files: list[str]) -> None:
        self.files = files
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(SUPPORTED_COMMANDS),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        cmd = tool_request["command"]["name"]
        if cmd == "lookup.query":
            return {
                "request_id": tool_request["request_id"],
                "status": "ok",
                "recall_packet": {
                    "searched_sources": ["decisions", "candidates"],
                    "corpus_version": "2026-06-25T00:00:00Z",
                    "matches": [],
                    "unknown_scope": list(self.files),
                    "allowed_next_actions": ["proceed"],
                },
            }
        # Should not reach preflight.run when guard fires
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "staged": _STAGED_PREFLIGHT,
        }


class _CoveredDaemon:
    """Daemon where files have coverage (matches exist)."""

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
        if cmd == "lookup.query":
            return {
                "request_id": tool_request["request_id"],
                "status": "ok",
                "recall_packet": {
                    "searched_sources": ["decisions"],
                    "corpus_version": "2026-06-25T00:00:00Z",
                    "matches": [
                        {
                            "kind": "decision",
                            "decision_id": "DEC-12",
                            "title": "Use structured logging",
                            "source_link": "local://spec.md",
                            "excerpt": "All services use structured logging.",
                        }
                    ],
                    "unknown_scope": [],
                    "allowed_next_actions": ["proceed"],
                },
            }
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "staged": _STAGED_PREFLIGHT,
        }


class _PartialCoverageDaemon:
    """Daemon where some files are covered and some are unknown."""

    def __init__(self, known_files: list[str], unknown_files: list[str]) -> None:
        self.known_files = known_files
        self.unknown_files = unknown_files
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(SUPPORTED_COMMANDS),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        cmd = tool_request["command"]["name"]
        if cmd == "lookup.query":
            return {
                "request_id": tool_request["request_id"],
                "status": "ok",
                "recall_packet": {
                    "searched_sources": ["decisions"],
                    "corpus_version": "2026-06-25T00:00:00Z",
                    "matches": [
                        {
                            "kind": "decision",
                            "decision_id": "DEC-5",
                            "title": "Coverage exists",
                        }
                    ],
                    "unknown_scope": list(self.unknown_files),
                    "allowed_next_actions": ["proceed"],
                },
            }
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "staged": _STAGED_PREFLIGHT,
        }


class _NoLookupDaemon:
    """Daemon that does NOT advertise lookup.query."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        commands = [c for c in SUPPORTED_COMMANDS if c != "lookup.query"]
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "staged": _STAGED_PREFLIGHT,
        }


class _ErrorLookupDaemon:
    """Daemon that raises an error on lookup.query."""

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
        if cmd == "lookup.query":
            raise DaemonConnectionError(
                "cannot reach daemon",
                daemon_endpoint="http://127.0.0.1:37373",
            )
        return {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "staged": _STAGED_PREFLIGHT,
        }


# ---------------------------------------------------------------------------
# AC-1: Un-ingested files trigger no-fire fast-path
# ---------------------------------------------------------------------------


class TestNoCoverageNoFire:
    """Files with zero coverage produce an explicit no-fire decision."""

    @pytest.mark.asyncio
    async def test_all_files_unknown_returns_no_fire(self, monkeypatch):
        """When all files are in unknown_scope with no matches, guard fires."""
        files = ["src/new_feature.py", "tests/test_new.py"]
        daemon = _NoCoverageDaemon(files=files)
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": files})
        response = json.loads(content[0].text)

        assert response["status"] == "no_fire"
        assert response["reason"] == "coverage_guard"
        assert response["session_directive"] == {"mode": "continue"}
        assert set(response["guarded_files"]) == set(files)

    @pytest.mark.asyncio
    async def test_no_fire_stages_all_skipped(self, monkeypatch):
        """No-fire response marks all stages as skipped with reason."""
        files = ["brand_new.rs"]
        daemon = _NoCoverageDaemon(files=files)
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": files})
        response = json.loads(content[0].text)

        stages = response["stages"]
        for stage_name in ("capture", "projection", "lookup", "enforcement"):
            assert stages[stage_name]["status"] == "skipped"
            assert stages[stage_name]["reason"] == "no_coverage"

    @pytest.mark.asyncio
    async def test_no_fire_does_not_dispatch_preflight_run(self, monkeypatch):
        """Guard short-circuits: no preflight.run is sent to daemon."""
        files = ["untracked.py"]
        daemon = _NoCoverageDaemon(files=files)
        monkeypatch.setattr(server, "_client", lambda: daemon)

        await server.call_tool("bicameral.preflight", {"files": files})

        # Only the lookup.query should have been dispatched, not preflight.run
        commands = [r["command"]["name"] for r in daemon.requests]
        assert "lookup.query" in commands
        assert "preflight.run" not in commands

    @pytest.mark.asyncio
    async def test_no_fire_has_request_id(self, monkeypatch):
        """No-fire response includes a request_id for traceability."""
        files = ["new_file.py"]
        daemon = _NoCoverageDaemon(files=files)
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool("bicameral.preflight", {"files": files})
        response = json.loads(content[0].text)

        assert "request_id" in response
        assert len(response["request_id"]) > 0


# ---------------------------------------------------------------------------
# AC-2: Covered files proceed to full preflight
# ---------------------------------------------------------------------------


class TestCoveredFilesProceed:
    """Files with ledger coverage bypass the guard and run full preflight."""

    @pytest.mark.asyncio
    async def test_files_with_matches_proceed_to_preflight(self, monkeypatch):
        """When lookup returns matches, full preflight pipeline runs."""
        daemon = _CoveredDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.preflight",
            {"files": ["src/lib.rs"], "symbols": ["DecisionLedger"]},
        )
        response = json.loads(content[0].text)

        # Full preflight response (not no_fire)
        assert response["status"] == "ok"
        assert "stages" in response
        assert response["stages"]["lookup"]["status"] == "completed"

        # Both lookup.query (guard) and preflight.run (full) dispatched
        commands = [r["command"]["name"] for r in daemon.requests]
        assert "lookup.query" in commands
        assert "preflight.run" in commands

    @pytest.mark.asyncio
    async def test_partial_coverage_proceeds_to_preflight(self, monkeypatch):
        """When some files have coverage, full preflight runs (conservative)."""
        daemon = _PartialCoverageDaemon(
            known_files=["src/existing.py"],
            unknown_files=["src/new.py"],
        )
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.preflight",
            {"files": ["src/existing.py", "src/new.py"]},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "ok"
        commands = [r["command"]["name"] for r in daemon.requests]
        assert "preflight.run" in commands


# ---------------------------------------------------------------------------
# AC-3: Guard fails open on errors, missing capabilities, or no files
# ---------------------------------------------------------------------------


class TestFailOpen:
    """Guard fails open — any uncertainty proceeds with full preflight."""

    @pytest.mark.asyncio
    async def test_no_lookup_capability_proceeds(self, monkeypatch):
        """When daemon doesn't advertise lookup.query, guard is bypassed."""
        daemon = _NoLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.preflight",
            {"files": ["src/main.py"]},
        )
        response = json.loads(content[0].text)

        # Full preflight runs (guard bypassed)
        assert response["status"] == "ok"
        commands = [r["command"]["name"] for r in daemon.requests]
        assert "preflight.run" in commands

    @pytest.mark.asyncio
    async def test_lookup_error_proceeds_to_preflight(self, monkeypatch):
        """When lookup.query fails, guard falls through to full preflight."""
        daemon = _ErrorLookupDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.preflight",
            {"files": ["src/main.py"]},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "ok"
        commands = [r["command"]["name"] for r in daemon.requests]
        assert "preflight.run" in commands

    @pytest.mark.asyncio
    async def test_no_files_param_proceeds(self, monkeypatch):
        """When no files are provided, guard is not activated."""
        daemon = _CoveredDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.preflight",
            {"symbols": ["SomeSymbol"], "branch": "main"},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "ok"
        # Only preflight.run should be dispatched (no guard lookup)
        commands = [r["command"]["name"] for r in daemon.requests]
        assert commands == ["preflight.run"]

    @pytest.mark.asyncio
    async def test_empty_files_list_proceeds(self, monkeypatch):
        """When files list is empty, guard is not activated."""
        daemon = _CoveredDaemon()
        monkeypatch.setattr(server, "_client", lambda: daemon)

        content = await server.call_tool(
            "bicameral.preflight",
            {"files": [], "branch": "main"},
        )
        response = json.loads(content[0].text)

        assert response["status"] == "ok"
        commands = [r["command"]["name"] for r in daemon.requests]
        assert commands == ["preflight.run"]


# ---------------------------------------------------------------------------
# Unit tests for check_coverage directly
# ---------------------------------------------------------------------------


class TestCheckCoverageUnit:
    """Direct unit tests for the coverage check logic."""

    @pytest.mark.asyncio
    async def test_returns_true_when_all_unknown(self):
        """Returns True (no coverage) when all files are in unknown_scope."""
        files = ["a.py", "b.py"]
        daemon = _NoCoverageDaemon(files=files)
        result = await check_coverage(
            client=daemon,
            files=files,
            supported_commands=tuple(SUPPORTED_COMMANDS),
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_matches_exist(self):
        """Returns False (has coverage) when matches are returned."""
        daemon = _CoveredDaemon()
        result = await check_coverage(
            client=daemon,
            files=["src/lib.rs"],
            supported_commands=tuple(SUPPORTED_COMMANDS),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_lookup_not_supported(self):
        """Returns False when lookup.query is not in supported_commands."""
        daemon = _NoCoverageDaemon(files=["a.py"])
        commands = tuple(c for c in SUPPORTED_COMMANDS if c != "lookup.query")
        result = await check_coverage(
            client=daemon,
            files=["a.py"],
            supported_commands=commands,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_daemon_error(self):
        """Returns False (fail open) when daemon raises an error."""
        daemon = _ErrorLookupDaemon()
        result = await check_coverage(
            client=daemon,
            files=["a.py"],
            supported_commands=tuple(SUPPORTED_COMMANDS),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_file_not_in_unknown_scope(self):
        """Returns False when a file is absent from unknown_scope."""
        # Daemon reports only some files as unknown
        daemon = _NoCoverageDaemon(files=["a.py"])  # Only reports a.py as unknown
        result = await check_coverage(
            client=daemon,
            files=["a.py", "b.py"],  # b.py not in unknown_scope
            supported_commands=tuple(SUPPORTED_COMMANDS),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_non_ok_status(self):
        """Returns False when daemon returns non-ok status."""

        class _BadStatusDaemon:
            async def send_tool_request(self, tool_request: dict) -> dict:
                return {
                    "request_id": tool_request["request_id"],
                    "status": "error",
                    "message": "internal error",
                }

        daemon = _BadStatusDaemon()
        result = await check_coverage(
            client=daemon,
            files=["a.py"],
            supported_commands=tuple(SUPPORTED_COMMANDS),
        )
        assert result is False
