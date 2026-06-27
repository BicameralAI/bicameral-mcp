"""Conformance tests for relevance-time context packets (issue #613)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import server
from responses import format_context_packet_response
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS
from tool_schemas import tool_for_name
from version import TOOLREQUEST_PROTOCOL_VERSION


class _ContextDaemon:
    def __init__(
        self, *, commands: list[str] | None = None, response: dict[str, Any] | None = None
    ):
        self.commands = commands if commands is not None else list(SUPPORTED_COMMANDS)
        self.response = response or _CONTEXT_RESPONSE
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": self.commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {"request_id": tool_request["request_id"], **self.response}


_CONTEXT_ARGS = {
    "query": "update billing retry behavior",
    "ticket": "BILL-123",
    "branch": "feature/retry-timeout",
    "pr": "https://github.com/example/repo/pull/42",
    "repo": "example/repo",
    "files": ["src/billing/retry.py"],
    "symbols": ["RetryPolicy"],
    "code_region": {"path": "src/billing/retry.py", "start_line": 20, "end_line": 44},
    "feature_area": "billing",
    "agent_session_context": {"checkpoint": "pre_work", "tool": "codex"},
    "planned_action": "change timeout default",
    "checkpoint_hint": "pre_work",
    "scope": "trusted_corpus",
    "include_context": True,
    "actor_id": "agent-1",
    "workspace": "/repo",
    "unknown_local_knob": "must not be forwarded",
}

_CONTEXT_RESPONSE = {
    "status": "ok",
    "context_packet": {
        "packet_id": "rp_613",
        "request": {
            "intent": "lookup",
            "checkpoint_hint": "pre_work",
            "query": "update billing retry behavior",
            "files": ["src/billing/retry.py"],
        },
        "corpus": {
            "corpus_id": "workspace",
            "version": "idx-123",
            "authority_spine": "decision_ledger",
            "trusted_sources": ["decision_ledger", "product_docs"],
        },
        "searched_sources": [
            {"source_id": "decision_ledger", "status": "searched"},
            {"source_id": "product_docs", "status": "searched"},
        ],
        "matches": [
            {
                "match_id": "DEC-7",
                "kind": "accepted_decision",
                "title": "Retry defaults",
                "summary": "Billing retries use a bounded timeout.",
                "authority": "canonical",
                "evidence_refs": ["ev-1"],
                "relevance_reasons": ["file overlap", "feature area"],
                "freshness_state": "current",
                "review_state": "accepted",
                "risk": "medium",
                "confidence": 0.84,
                "rationale": "Daemon-ranked corpus match.",
                "required_actions": [],
            },
            {
                "match_id": "cand-9",
                "kind": "contradiction_candidate",
                "title": "Ticket asks for different timeout",
                "summary": "Source-only ticket may conflict with trusted corpus.",
                "authority": "candidate",
                "evidence_refs": ["ticket:BILL-123"],
                "relevance_reasons": ["ticket overlap"],
                "freshness_state": "unknown",
                "review_state": "review_needed",
                "risk": "high",
                "confidence": 0.61,
                "required_actions": ["review_needed", "request_correction"],
            },
        ],
        "unknown_scope": [
            {
                "scope": "private product notes",
                "reason": "permission_missing",
                "expand_action": "connect_product_notes",
            }
        ],
        "allowed_next_actions": ["expand_scope", "request_correction"],
        "receipt_ref": "lookup-trace-1",
    },
    "session_directive": {"mode": "continue"},
}


def test_context_tool_schema_exposes_relevance_time_hints():
    tool = tool_for_name("bicameral.context")

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
        "planned_action",
        "checkpoint_hint",
    ):
        assert key in props


@pytest.mark.asyncio
async def test_context_maps_to_lookup_query_without_local_ranking(monkeypatch):
    daemon = _ContextDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.context", dict(_CONTEXT_ARGS))
    rendered = json.loads(content[0].text)

    assert MCP_TOOL_COMMANDS["bicameral.context"] == "lookup.query"
    assert len(daemon.requests) == 1
    request = daemon.requests[0]
    assert request["command"]["name"] == "lookup.query"
    assert request["authority"]["audit_metadata"]["mcp_tool"] == "bicameral.context"
    assert "unknown_local_knob" not in request["command"]["params"]
    assert "actor_id" not in request["command"]["params"]
    assert request["command"]["params"]["ticket"] == "BILL-123"
    assert request["command"]["params"]["agent_session_context"] == {
        "checkpoint": "pre_work",
        "tool": "codex",
    }
    assert rendered["context_packet"]["matches"][0]["authority"] == "canonical"
    assert rendered["context_packet"]["matches"][1]["authority"] == "candidate"


@pytest.mark.asyncio
async def test_context_is_capability_gated_on_lookup_query(monkeypatch):
    daemon = _ContextDaemon(commands=[c for c in SUPPORTED_COMMANDS if c != "lookup.query"])
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.context", {"ticket": "BILL-123"})
    rendered = json.loads(content[0].text)

    assert rendered["status"] == "error"
    assert rendered["error_code"] == "daemon_capability_error"
    assert rendered["recovery"]["requested_tool"] == "bicameral.context"
    assert rendered["recovery"]["requested_command"] == "lookup.query"
    assert daemon.requests == []


def test_context_renderer_preserves_source_distinction_and_required_actions():
    rendered = json.loads(
        format_context_packet_response({"request_id": "req-1", **_CONTEXT_RESPONSE}).text
    )

    packet = rendered["context_packet"]
    assert packet["packet_id"] == "rp_613"
    assert packet["corpus"]["authority_spine"] == "decision_ledger"
    assert packet["matches"][0]["authority"] == "canonical"
    assert packet["matches"][1]["authority"] == "candidate"
    assert packet["matches"][1]["review_state"] == "review_needed"
    assert packet["matches"][1]["required_actions"] == ["review_needed", "request_correction"]
    assert packet["unknown_scope"][0]["reason"] == "permission_missing"
    assert packet["allowed_next_actions"] == ["expand_scope", "request_correction"]

    text = json.dumps(rendered).lower()
    for forbidden in ("safe to change", "no conflict", "compliant", "signoff approved"):
        assert forbidden not in text


def test_context_renderer_no_matches_does_not_infer_completeness():
    rendered = json.loads(
        format_context_packet_response(
            {
                "request_id": "req-empty",
                "status": "ok",
                "context_packet": {
                    "searched_sources": [{"source_id": "decision_ledger", "status": "searched"}],
                    "matches": [],
                    "unknown_scope": [{"scope": "wiki", "reason": "not_configured"}],
                },
            }
        ).text
    )

    packet = rendered["context_packet"]
    assert packet["matches"] == []
    assert packet["unknown_scope"] == [{"scope": "wiki", "reason": "not_configured"}]
    assert "does not imply absence outside searched or configured scope" in packet["no_match_note"]


@pytest.mark.asyncio
async def test_context_call_creates_no_local_artifacts(monkeypatch, tmp_path):
    daemon = _ContextDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)
    monkeypatch.chdir(tmp_path)

    before = _snapshot_local_files(tmp_path)
    await server.call_tool("bicameral.context", {"ticket": "BILL-123"})
    after = _snapshot_local_files(tmp_path)

    assert before == after


def _snapshot_local_files(workspace: Path) -> set[Path]:
    found: set[Path] = set()
    for path in workspace.rglob("*"):
        if path.is_file():
            found.add(path)
    return found
