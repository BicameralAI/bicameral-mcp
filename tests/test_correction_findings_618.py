"""Conformance tests for correction-capture finding surfacing (issue #618)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import server
from responses import format_correction_findings_response
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS
from tool_schemas import tool_for_name
from version import TOOLREQUEST_PROTOCOL_VERSION


class _CorrectionFindingsDaemon:
    def __init__(
        self, *, commands: list[str] | None = None, response: dict[str, Any] | None = None
    ):
        self.commands = commands if commands is not None else list(SUPPORTED_COMMANDS)
        self.response = response or _FINDINGS_RESPONSE
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": self.commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {"request_id": tool_request["request_id"], **self.response}


_FINDINGS_ARGS = {
    "query": "payment retry update",
    "ticket": "BILL-456",
    "branch": "feature/retry-window",
    "pr": "https://github.com/example/repo/pull/456",
    "repo": "example/repo",
    "files": ["src/billing/retry.py"],
    "symbols": ["RetryWindow"],
    "code_region": {"path": "src/billing/retry.py", "start_line": 10, "end_line": 38},
    "feature_area": "billing",
    "agent_session_context": {"checkpoint": "pre_write", "tool": "codex"},
    "planned_action": "change retry window",
    "checkpoint_hint": "pre_write",
    "scope": "correction_capture",
    "finding_status": "active",
    "severity": "high",
    "include_correction_findings": True,
    "include_context": True,
    "actor_id": "agent-618",
    "workspace": "/repo",
    "unknown_local_knob": "must not be forwarded",
}

_FINDINGS_RESPONSE = {
    "status": "ok",
    "correction_findings_packet": {
        "packet_id": "cfp-618",
        "request": {
            "pr": "https://github.com/example/repo/pull/456",
            "checkpoint_hint": "pre_write",
            "scope": "correction_capture",
        },
        "searched_sources": [
            {"source_id": "decision_ledger", "status": "searched"},
            {"source_id": "product_docs", "status": "searched"},
        ],
        "findings": [
            {
                "finding_id": "cf-1",
                "summary": "Retry window implementation may require product doc update.",
                "affected_code_region": {
                    "provider": "github",
                    "repo": "example/repo",
                    "path": "src/billing/retry.py",
                    "start_line": 10,
                    "end_line": 38,
                },
                "trusted_corpus_ref": {
                    "kind": "accepted_decision",
                    "id": "DEC-7",
                    "title": "Retry policy",
                },
                "source_doc_ref": {
                    "provider": "gdrive",
                    "doc_id": "doc-123",
                    "section": "Billing retries",
                },
                "decision_refs": ["DEC-7"],
                "constraint_refs": ["CON-2"],
                "evidence_refs": ["ev-9"],
                "candidate_change": "Update source doc to describe new retry window.",
                "authority": "candidate",
                "severity": "high",
                "confidence_bps": 8700,
                "review_state": "review_needed",
                "suggested_action": "update_source_doc",
                "required_actions": ["review_needed"],
                "allowed_next_actions": ["bicameral.request_correction", "bicameral.context"],
            }
        ],
        "unknown_scope": [{"scope": "private roadmap docs", "reason": "permission_missing"}],
        "allowed_next_actions": ["request_correction", "open_review"],
        "review_handoff": {
            "target_tool": "bicameral.request_correction",
            "requires_approval": True,
        },
        "receipt_ref": "cf-trace-1",
    },
    "session_directive": {"mode": "continue"},
}


def test_correction_findings_schema_exposes_workflow_hints():
    tool = tool_for_name("bicameral.correction_findings")

    assert tool is not None
    props = tool.inputSchema["properties"]
    for key in (
        "ticket",
        "branch",
        "pr",
        "repo",
        "files",
        "code_region",
        "feature_area",
        "agent_session_context",
        "finding_status",
        "severity",
    ):
        assert key in props


@pytest.mark.asyncio
async def test_correction_findings_maps_to_lookup_query(monkeypatch):
    daemon = _CorrectionFindingsDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.correction_findings", dict(_FINDINGS_ARGS))
    rendered = json.loads(content[0].text)

    assert MCP_TOOL_COMMANDS["bicameral.correction_findings"] == "lookup.query"
    assert len(daemon.requests) == 1
    request = daemon.requests[0]
    assert request["command"]["name"] == "lookup.query"
    assert request["authority"]["audit_metadata"]["mcp_tool"] == "bicameral.correction_findings"
    assert "unknown_local_knob" not in request["command"]["params"]
    assert "actor_id" not in request["command"]["params"]
    assert request["command"]["params"]["finding_status"] == "active"
    assert request["command"]["params"]["include_correction_findings"] is True
    assert rendered["correction_findings_packet"]["findings"][0]["authority"] == "candidate"


@pytest.mark.asyncio
async def test_correction_findings_are_capability_gated_on_lookup_query(monkeypatch):
    daemon = _CorrectionFindingsDaemon(
        commands=[command for command in SUPPORTED_COMMANDS if command != "lookup.query"]
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.correction_findings", {"pr": "1"})
    rendered = json.loads(content[0].text)

    assert rendered["status"] == "error"
    assert rendered["error_code"] == "daemon_capability_error"
    assert rendered["recovery"]["requested_tool"] == "bicameral.correction_findings"
    assert rendered["recovery"]["requested_command"] == "lookup.query"
    assert daemon.requests == []


def test_correction_findings_renderer_preserves_review_handoff_and_source_distinction():
    rendered = json.loads(
        format_correction_findings_response({"request_id": "req-618", **_FINDINGS_RESPONSE}).text
    )

    packet = rendered["correction_findings_packet"]
    finding = packet["findings"][0]
    assert finding["affected_code_region"]["path"] == "src/billing/retry.py"
    assert finding["trusted_corpus_ref"]["kind"] == "accepted_decision"
    assert finding["source_doc_ref"]["provider"] == "gdrive"
    assert finding["decision_refs"] == ["DEC-7"]
    assert finding["constraint_refs"] == ["CON-2"]
    assert finding["evidence_refs"] == ["ev-9"]
    assert finding["suggested_action"] == "update_source_doc"
    assert finding["review_state"] == "review_needed"
    assert finding["allowed_next_actions"] == ["bicameral.request_correction", "bicameral.context"]
    assert packet["review_handoff"]["target_tool"] == "bicameral.request_correction"
    assert packet["unknown_scope"][0]["reason"] == "permission_missing"

    text = json.dumps(rendered).lower()
    for forbidden in ("canonical update applied", "decision accepted", "safe to merge"):
        assert forbidden not in text


def test_correction_findings_renderer_no_findings_does_not_infer_absence():
    rendered = json.loads(
        format_correction_findings_response(
            {
                "request_id": "req-empty",
                "status": "ok",
                "correction_findings_packet": {
                    "searched_sources": [{"source_id": "decision_ledger", "status": "searched"}],
                    "findings": [],
                    "unknown_scope": [{"scope": "wiki", "reason": "not_configured"}],
                },
            }
        ).text
    )

    packet = rendered["correction_findings_packet"]
    assert packet["findings"] == []
    assert packet["unknown_scope"] == [{"scope": "wiki", "reason": "not_configured"}]
    assert (
        "does not imply absence outside searched or configured scope" in packet["no_findings_note"]
    )


@pytest.mark.asyncio
async def test_correction_findings_call_creates_no_local_artifacts(monkeypatch, tmp_path):
    daemon = _CorrectionFindingsDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.chdir(tmp_path)

    before = _snapshot_local_files(tmp_path)
    await server.call_tool("bicameral.correction_findings", {"pr": "456"})
    after = _snapshot_local_files(tmp_path)

    assert before == after


def _snapshot_local_files(workspace: Path) -> set[Path]:
    return {path for path in workspace.rglob("*") if path.is_file()}
