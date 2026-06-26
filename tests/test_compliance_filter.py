"""Sociable tests for _pending_compliance_checks scope-filter and budget (#504).

Tests exercise the full call_tool → daemon → filter → formatter path using a
recording daemon that returns pre-seeded _pending_compliance_checks.  The filter
runs in its production location (server.call_tool) — no internal seams mocked.

Acceptance criteria from #504:
  AC-1  Scope filter: seed N≥20 checks across two packages, call preflight
        with file_paths in one package, assert only that package's checks.
  AC-2  Budget: seed enough checks to exceed budget, assert truncation digest.
  AC-3  Zero-overlap: file_paths that don't overlap any check, assert absent.
  AC-4  Backwards-compat: within scope and below budget, byte-identical.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import server
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION


@pytest.fixture(autouse=True)
def _reset_approval_gate():
    server._approval_gate.clear()
    yield
    server._approval_gate.clear()


def _make_check(file_path: str, decision_id: str = "DEC-1") -> dict[str, Any]:
    """Build a minimal compliance check dict."""
    return {
        "decision_id": decision_id,
        "file_path": file_path,
        "verdict": "pending",
        "symbol": f"sym_{file_path.replace('/', '_')}",
    }


def _make_check_with_region(file_path: str, decision_id: str = "DEC-1") -> dict[str, Any]:
    """Build a compliance check with nested code_region.file_path."""
    return {
        "decision_id": decision_id,
        "code_region": {"file_path": file_path, "start_line": 1, "end_line": 10},
        "verdict": "pending",
    }


class _ComplianceDaemon:
    """Fake daemon that injects _pending_compliance_checks into preflight responses."""

    def __init__(self, checks: list[dict[str, Any]]) -> None:
        self.checks = checks
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        command = tool_request["command"]["name"]
        base: dict[str, Any] = {
            "request_id": tool_request["request_id"],
            "status": "ok",
            "responded_at": "2026-06-22T00:00:00Z",
        }
        if command == "preflight.run":
            base["staged"] = {
                "lookup": {"status": "completed", "decision_refs": [], "limitations": []},
                "session_directive": {"mode": "continue"},
            }
        if self.checks:
            base["_pending_compliance_checks"] = list(self.checks)
            base["_pending_flow_id"] = "flow-test-504"
            base["_sync_guidance"] = (
                f"New commit detected — {len(self.checks)} decision(s) need "
                "compliance verification."
            )
        return base


def _patch_daemon(monkeypatch, daemon: _ComplianceDaemon) -> None:
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.setenv("BICAMERAL_ACTOR_ID", "test-actor")
    monkeypatch.setenv("BICAMERAL_WORKSPACE", "/repo")
    monkeypatch.delenv("BICAMERAL_DAEMON_URL", raising=False)
    monkeypatch.delenv("BICAMERAL_BOT_DAEMON_URL", raising=False)


# ---------------------------------------------------------------------------
# AC-1: Scope filter — file_paths in one package → only that package's checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_filter_keeps_only_caller_package(monkeypatch):
    """Seed 25 checks across pilot/ and site/, call with site/ files only."""
    checks = []
    for i in range(15):
        checks.append(_make_check(f"pilot/mcp/handler_{i}.py", f"DEC-{i}"))
    for i in range(10):
        checks.append(_make_check(f"site/src/component_{i}.svelte", f"DEC-S{i}"))

    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["site/src/routes/blog/+page.svelte"]},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("result", {}).get("_pending_compliance_checks")
    assert result_checks is not None
    assert isinstance(result_checks, list)
    assert len(result_checks) == 10

    for check in result_checks:
        assert check["file_path"].startswith("site/")


@pytest.mark.asyncio
async def test_scope_filter_with_code_region(monkeypatch):
    """Checks with nested code_region.file_path are also scope-filtered."""
    checks = [
        _make_check_with_region("pilot/mcp/handler.py", "DEC-1"),
        _make_check_with_region("site/src/app.svelte", "DEC-2"),
    ]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["site/src/routes/+layout.svelte"]},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("result", {}).get("_pending_compliance_checks")
    assert isinstance(result_checks, list)
    assert len(result_checks) == 1
    assert result_checks[0]["code_region"]["file_path"] == "site/src/app.svelte"


# ---------------------------------------------------------------------------
# AC-2: Budget — exceed budget → truncation digest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_truncation_returns_digest(monkeypatch):
    """Seed enough checks to blow the 16k char budget, assert digest shape."""
    checks = []
    for i in range(200):
        checks.append(_make_check(f"pilot/mcp/module_{i:04d}_longname_padding.py", f"DEC-{i}"))

    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["pilot/mcp/server.py"]},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("result", {}).get("_pending_compliance_checks")

    assert isinstance(result_checks, dict), "Expected truncation digest dict"
    assert result_checks["truncated"] is True
    assert result_checks["total"] == 200
    assert result_checks["kept"] < 200
    assert "hint" in result_checks
    assert isinstance(result_checks.get("items"), list)

    guidance = parsed.get("result", {}).get("_sync_guidance")
    assert guidance is not None
    assert "200" in guidance
    assert "bicameral.history" in guidance


@pytest.mark.asyncio
async def test_budget_within_limit_returns_list(monkeypatch):
    """Small check set below budget is returned as-is (list, not digest)."""
    checks = [_make_check("pilot/mcp/handler.py", f"DEC-{i}") for i in range(3)]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["pilot/mcp/server.py"]},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("result", {}).get("_pending_compliance_checks")
    assert isinstance(result_checks, list)
    assert len(result_checks) == 3


# ---------------------------------------------------------------------------
# AC-3: Zero-overlap — file_paths outside ingested scope → no checks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_overlap_removes_all_checks(monkeypatch):
    """Caller paths in unrelated/ have zero overlap with pilot/ checks."""
    checks = [_make_check(f"pilot/mcp/handler_{i}.py", f"DEC-{i}") for i in range(20)]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["unrelated/frontend/app.tsx", "unrelated/config.yaml"]},
    )
    parsed = json.loads(content[0].text)
    result = parsed.get("result", {})

    assert "_pending_compliance_checks" not in result
    assert "_sync_guidance" not in result
    assert "_pending_flow_id" not in result


@pytest.mark.asyncio
async def test_zero_overlap_with_lookup_tool(monkeypatch):
    """Zero-overlap also works for lookup (which accepts files param)."""
    checks = [_make_check("pilot/mcp/handler.py", "DEC-1")]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.lookup",
        {"files": ["docs/readme.md"]},
    )
    parsed = json.loads(content[0].text)
    assert "_pending_compliance_checks" not in parsed


# ---------------------------------------------------------------------------
# AC-4: Backwards-compat — within scope and below budget → byte-identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_within_scope_below_budget_byte_identical(monkeypatch):
    """Checks all in caller scope and under budget → identical to raw daemon payload."""
    checks = [
        _make_check("pilot/mcp/handler_0.py", "DEC-0"),
        _make_check("pilot/mcp/handler_1.py", "DEC-1"),
        _make_check("pilot/mcp/handler_2.py", "DEC-2"),
    ]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.preflight",
        {"files": ["pilot/mcp/server.py"]},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("result", {}).get("_pending_compliance_checks")

    assert isinstance(result_checks, list)
    assert result_checks == checks


@pytest.mark.asyncio
async def test_no_file_paths_passes_all_checks_through(monkeypatch):
    """When caller provides no file_paths, all checks pass through (budgeted)."""
    checks = [_make_check(f"pilot/mcp/handler_{i}.py", f"DEC-{i}") for i in range(5)]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.history",
        {"decision_id": "DEC-1"},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("_pending_compliance_checks")
    assert isinstance(result_checks, list)
    assert len(result_checks) == 5


# ---------------------------------------------------------------------------
# Non-preflight tool — verify filter applies to generic format_tool_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_filter_on_non_preflight_tool(monkeypatch):
    """scope filter also works for non-preflight tools like search (no files param)."""
    checks = [_make_check("pilot/mcp/handler.py", "DEC-1")]
    daemon = _ComplianceDaemon(checks)
    _patch_daemon(monkeypatch, daemon)

    content = await server.call_tool(
        "bicameral.search",
        {"query": "cherry-pick"},
    )
    parsed = json.loads(content[0].text)
    result_checks = parsed.get("_pending_compliance_checks")
    assert isinstance(result_checks, list)
    assert len(result_checks) == 1


# ---------------------------------------------------------------------------
# Unit tests for sync_payload_filter module
# ---------------------------------------------------------------------------


class TestScopeFilterChecks:
    def test_empty_checks(self):
        from sync_payload_filter import scope_filter_checks

        result = scope_filter_checks([], ["pilot/mcp/server.py"])
        assert result == []

    def test_empty_caller_paths_returns_all(self):
        from sync_payload_filter import scope_filter_checks

        checks = [_make_check("pilot/mcp/a.py"), _make_check("site/b.svelte")]
        result = scope_filter_checks(checks, [])
        assert result == checks

    def test_filters_by_top_level_dir(self):
        from sync_payload_filter import scope_filter_checks

        checks = [
            _make_check("pilot/mcp/a.py"),
            _make_check("site/src/b.svelte"),
            _make_check("pilot/cli/c.py"),
        ]
        result = scope_filter_checks(checks, ["pilot/mcp/server.py"])
        assert len(result) == 2
        assert all(c["file_path"].startswith("pilot/") for c in result)

    def test_check_without_file_path_kept(self):
        from sync_payload_filter import scope_filter_checks

        checks = [{"decision_id": "DEC-X", "verdict": "pending"}]
        result = scope_filter_checks(checks, ["pilot/mcp/server.py"])
        assert len(result) == 1


class TestApplyBudget:
    def test_within_budget_returns_list(self):
        from sync_payload_filter import apply_budget

        checks = [_make_check("a.py")]
        result = apply_budget(checks, budget_chars=10000)
        assert isinstance(result, list)

    def test_over_budget_returns_digest(self):
        from sync_payload_filter import apply_budget

        checks = [_make_check(f"module_{i:04d}_long_padding.py") for i in range(500)]
        result = apply_budget(checks, budget_chars=1000)
        assert isinstance(result, dict)
        assert result["truncated"] is True
        assert result["total"] == 500
        assert result["kept"] < 500


class TestFilterComplianceChecks:
    def test_no_checks_is_noop(self):
        from sync_payload_filter import filter_pending_checks

        response: dict[str, Any] = {"status": "ok"}
        filter_pending_checks(response, ["a.py"])
        assert "_pending_compliance_checks" not in response

    def test_zero_overlap_removes_keys(self):
        from sync_payload_filter import filter_pending_checks

        response: dict[str, Any] = {
            "status": "ok",
            "_pending_compliance_checks": [_make_check("pilot/mcp/a.py")],
            "_pending_flow_id": "flow-1",
            "_sync_guidance": "guidance text",
        }
        filter_pending_checks(response, ["unrelated/b.ts"])
        assert "_pending_compliance_checks" not in response
        assert "_pending_flow_id" not in response
        assert "_sync_guidance" not in response
