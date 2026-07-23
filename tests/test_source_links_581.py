"""Source-link and EvidenceReference rendering for search/history/inspection."""

from __future__ import annotations

import json
from typing import Any

import pytest

import server
from responses import format_source_link_response
from tool_request import SUPPORTED_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION


class _SourceLinkDaemon:
    def __init__(self, response: dict[str, Any], *, commands: list[str] | None = None):
        self.response = response
        self.commands = commands if commands is not None else list(SUPPORTED_COMMANDS)
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": self.commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {"request_id": tool_request["request_id"], **self.response}


def test_source_link_renderer_labels_source_only_search_results_as_advisory():
    rendered = json.loads(
        format_source_link_response(
            {
                "request_id": "req-581",
                "status": "ok",
                "result": {
                    "matches": [
                        {
                            "kind": "decision",
                            "decision_id": "DEC-7",
                            "title": "Retry policy",
                            "source_uri": "gdrive://doc-1#heading=h",
                            "source_kind": "google_doc",
                            "source_snapshot_id": "snap-1",
                            "evidence_ref_id": "ev-1",
                            "pointer": {"heading": "Retry"},
                            "locator": {"provider": "gdrive", "doc_id": "doc-1"},
                            "excerpt": "Retries must stop after three attempts.",
                        }
                    ]
                },
            },
            surface="search",
        ).text
    )

    match = rendered["matches"][0]
    assert match["source_uri"] == "gdrive://doc-1#heading=h"
    assert match["source_kind"] == "google_doc"
    assert match["snapshot_id"] == "snap-1"
    assert match["evidence_ref_id"] == "ev-1"
    assert match["pointer"] == {"heading": "Retry"}
    assert match["locator"] == {"provider": "gdrive", "doc_id": "doc-1"}
    assert match["authority"] == "source_only_advisory"
    assert "not graph verification" in match["advisory_note"]
    assert "not compliance" in rendered["source_link_note"]


def test_search_renderer_accepts_the_bot_runtime_search_response_shape():
    """Bot's production SearchResponse uses results/evidence_reference_ids."""
    rendered = json.loads(
        format_source_link_response(
            {
                "request_id": "req-real-search",
                "status": "ok",
                "result": {
                    "results": [
                        {
                            "type": "candidate",
                            "id": "cand-real-1",
                            "title": "integration-bot-mcp-marker",
                            "snippet": "revision-pinned journey evidence",
                            "source_id": "https://topology.invalid/events/1",
                            "source_snapshot_id": "snap-real-1",
                            "evidence_reference_ids": ["ev-real-1"],
                            "inspection_uri": "bicameral://evidence/candidate/cand-real-1/source",
                        }
                    ]
                },
            },
            surface="search",
        ).text
    )

    match = rendered["matches"][0]
    assert match["kind"] == "candidate"
    assert match["id"] == "cand-real-1"
    assert match["source_uri"] == "https://topology.invalid/events/1"
    assert match["source_link"] == "bicameral://evidence/candidate/cand-real-1/source"
    assert match["snapshot_id"] == "snap-real-1"
    assert match["evidence_ref_id"] == "ev-real-1"
    assert match["evidence_refs"] == ["ev-real-1"]
    assert match["excerpt"] == "revision-pinned journey evidence"


def test_source_link_renderer_labels_verified_binding_without_compliance_claim():
    rendered = json.loads(
        format_source_link_response(
            {
                "request_id": "req-581",
                "status": "ok",
                "result": {
                    "decision_or_candidate_id": "DEC-7",
                    "graph_snapshot_id": "graph-1",
                    "bindings": [
                        {
                            "symbol": "src/retry.py:RetryPolicy",
                            "evidence_state": "verified",
                            "currentness": "current",
                            "validated_sha": "abc123",
                            "source_link": "github://repo/src/retry.py#L10-L20",
                            "evidence_refs": ["ev-bind-1"],
                        }
                    ],
                },
            },
            surface="binding.inspect",
        ).text
    )

    binding = rendered["bindings"][0]
    assert binding["authority"] == "verified_graph_binding"
    assert binding["graph_snapshot_id"] == "graph-1"
    assert binding["evidence_refs"] == ["ev-bind-1"]
    assert binding["currentness"] == "current"
    assert "does not infer compliance" in binding["advisory_note"]
    assert "merge-safety proof" in rendered["source_link_note"]


@pytest.mark.asyncio
async def test_search_tool_renders_source_links(monkeypatch):
    daemon = _SourceLinkDaemon(
        {
            "status": "ok",
            "result": {
                "matches": [
                    {
                        "kind": "decision",
                        "decision_id": "DEC-7",
                        "title": "Checkout behavior",
                        "source_link": "linear://issue/ABC-1",
                        "source_kind": "linear_issue",
                        "evidence_ref_id": "ev-search-1",
                        "citation": "ABC-1 comment 3",
                    }
                ],
                "binding_scope": {"status": "unsupported", "reason": "not projected"},
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.search", {"query": "checkout"})
    rendered = json.loads(content[0].text)

    assert daemon.requests[0]["command"]["name"] == "search.query"
    assert rendered["surface"] == "search"
    assert rendered["matches"][0]["source_link"] == "linear://issue/ABC-1"
    assert rendered["matches"][0]["source_kind"] == "linear_issue"
    assert rendered["matches"][0]["evidence_ref_id"] == "ev-search-1"
    assert rendered["binding_scope"]["status"] == "unsupported"


@pytest.mark.asyncio
async def test_history_tool_renders_source_links_on_decisions_and_events(monkeypatch):
    daemon = _SourceLinkDaemon(
        {
            "status": "ok",
            "result": {
                "decisions": [
                    {
                        "decision_id": "DEC-7",
                        "title": "Checkout behavior",
                        "source_uri": "mcp://session/sess-1",
                        "source_kind": "mcp_session",
                        "source_snapshot_id": "snap-hist-1",
                        "evidence_ref_id": "ev-hist-1",
                    }
                ],
                "events": [
                    {
                        "event_id": "evt-1",
                        "decision_id": "DEC-7",
                        "kind": "candidate_accepted",
                        "source_link": "mcp://session/sess-1#turn=4",
                        "evidence_refs": ["ev-hist-1"],
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool(
        "bicameral.history", {"decision_id": "DEC-7", "include_events": True}
    )
    rendered = json.loads(content[0].text)

    assert daemon.requests[0]["command"]["name"] == "history.list"
    assert rendered["surface"] == "history"
    assert rendered["decisions"][0]["source_uri"] == "mcp://session/sess-1"
    assert rendered["decisions"][0]["snapshot_id"] == "snap-hist-1"
    assert rendered["events"][0]["evidence_refs"] == ["ev-hist-1"]
    assert rendered["events"][0]["authority"] == "source_only_advisory"


@pytest.mark.asyncio
async def test_binding_inspect_renders_verified_source_links(monkeypatch):
    daemon = _SourceLinkDaemon(
        {
            "status": "ok",
            "result": {
                "decision_or_candidate_id": "DEC-7",
                "graph_snapshot_id": "graph-2",
                "bindings": [
                    {
                        "symbol": "src/checkout.py:Checkout",
                        "evidence_state": "verified",
                        "graph_readiness": "ready",
                        "source_uri": "github://repo/src/checkout.py#L7-L12",
                        "evidence_reference_id": "ev-bind-2",
                    }
                ],
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool(
        "bicameral.binding.inspect", {"decision_or_candidate_id": "DEC-7"}
    )
    rendered = json.loads(content[0].text)

    assert daemon.requests[0]["command"]["name"] == "binding.inspect"
    assert rendered["surface"] == "binding.inspect"
    assert rendered["decision_or_candidate_id"] == "DEC-7"
    assert rendered["bindings"][0]["authority"] == "verified_graph_binding"
    assert rendered["bindings"][0]["evidence_ref_id"] == "ev-bind-2"
    assert rendered["bindings"][0]["graph_readiness"] == "ready"


def test_source_link_renderer_does_not_emit_compliance_or_signoff_fields():
    rendered = json.loads(
        format_source_link_response(
            {
                "request_id": "req-581",
                "status": "ok",
                "result": {
                    "matches": [
                        {
                            "kind": "decision",
                            "decision_id": "DEC-7",
                            "source_link": "local://source",
                            "evidence_ref_id": "ev-1",
                        }
                    ]
                },
            },
            surface="search",
        ).text
    )

    match = rendered["matches"][0]
    assert "compliance_state" not in match
    assert "signoff_state" not in match
    assert "implementation_proof" not in match
