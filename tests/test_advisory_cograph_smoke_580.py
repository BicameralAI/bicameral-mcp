"""MCP -> bot advisory provenance golden-path smoke test (mcp#580)."""

from __future__ import annotations

import json
from typing import Any

import pytest

import server
from tool_request import SUPPORTED_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION


class _AdvisoryCographDaemon:
    """Fake bot daemon that records the thin-client golden path."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.source_snapshot_id = "snap-580-session-1"
        self.evidence_ref_id = "ev-580-command-output"
        self.candidate_id = "cand-580-retry-policy"
        self.decision_id = "DEC-580"
        self.correlation_id = "corr-580"
        self.session_id = "sess-580"

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(SUPPORTED_COMMANDS),
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        command = tool_request["command"]["name"]
        params = tool_request["command"]["params"]

        if command == "ingest.submit_local":
            return self._response(
                tool_request,
                {
                    "source_snapshot_id": self.source_snapshot_id,
                    "evidence_reference_ids": [self.evidence_ref_id],
                    "candidate_id": self.candidate_id,
                    "correlation_id": params["metadata"]["correlation_id"],
                    "session_id": params["metadata"]["mcp_session_id"],
                    "telemetry": [
                        {
                            "side": "mcp",
                            "event": "capture_context.sent",
                            "correlation_id": params["metadata"]["correlation_id"],
                        },
                        {
                            "side": "bot",
                            "event": "source_snapshot.created",
                            "source_snapshot_id": self.source_snapshot_id,
                            "evidence_ref_id": self.evidence_ref_id,
                        },
                    ],
                    "mutation": "source_snapshot_only",
                },
            )

        if command == "lookup.query":
            return self._response(
                tool_request,
                {
                    "recall_packet": {
                        "searched_sources": params.get("files", []),
                        "matches": [
                            {
                                "decision_id": self.decision_id,
                                "evidence_refs": [self.evidence_ref_id],
                                "source_link": "mcp://session/sess-580#command=pytest",
                            }
                        ],
                        "unknown_scope": [],
                    },
                    "correlation_id": self.correlation_id,
                    "mutation": "none",
                },
            )

        if command == "preflight.run":
            return {
                **self._response(
                    tool_request,
                    {
                        "relevant_decisions": [self.decision_id],
                        "source_snapshot_id": self.source_snapshot_id,
                        "evidence_reference_ids": [self.evidence_ref_id],
                        "graph_readiness": "source_only",
                        "correlation_id": self.correlation_id,
                        "mutation": "none",
                    },
                ),
                "staged": {
                    "capture": {"status": "completed", "evidence_refs": [self.evidence_ref_id]},
                    "projection": {"status": "source_only"},
                    "lookup": {
                        "status": "completed",
                        "decision_refs": [self.decision_id],
                        "limitations": ["source-only advisory smoke test"],
                    },
                    "enforcement": {"status": "not_configured"},
                    "session_directive": {"mode": "continue"},
                },
            }

        if command == "search.query":
            return self._response(
                tool_request,
                {
                    "matches": [
                        {
                            "kind": "candidate",
                            "id": self.candidate_id,
                            "title": "Retry failing daemon reads with backoff",
                            "source_uri": "mcp://session/sess-580",
                            "source_kind": "mcp_session",
                            "source_snapshot_id": self.source_snapshot_id,
                            "evidence_ref_id": self.evidence_ref_id,
                            "source_link": "mcp://session/sess-580#command=pytest",
                            "excerpt": "pytest -q passed after adding the retry guard.",
                            "currentness": "current",
                            "graph_readiness": "source_only",
                        }
                    ],
                    "correlation_id": self.correlation_id,
                    "binding_scope": {"status": "source_only", "reason": "no hosted graph"},
                    "mutation": "none",
                },
            )

        if command == "history.list":
            return self._response(
                tool_request,
                {
                    "decisions": [
                        {
                            "decision_id": self.decision_id,
                            "title": "Retry failing daemon reads with backoff",
                            "source_uri": "mcp://session/sess-580",
                            "source_kind": "mcp_session",
                            "source_snapshot_id": self.source_snapshot_id,
                            "evidence_ref_id": self.evidence_ref_id,
                        }
                    ],
                    "events": [
                        {
                            "event_id": "evt-580-candidate",
                            "decision_id": self.decision_id,
                            "kind": "candidate_created",
                            "source_link": "mcp://session/sess-580#turn=2",
                            "evidence_refs": [self.evidence_ref_id],
                        }
                    ],
                    "correlation_id": self.correlation_id,
                    "mutation": "none",
                },
            )

        if command == "binding.inspect":
            return self._response(
                tool_request,
                {
                    "decision_or_candidate_id": self.candidate_id,
                    "graph_snapshot_id": self.source_snapshot_id,
                    "bindings": [
                        {
                            "symbol": "src/retry.py:RetryPolicy",
                            "evidence_state": "source_only",
                            "graph_readiness": "source_only",
                            "source_uri": "mcp://session/sess-580#code_hint=retry",
                            "source_kind": "mcp_code_hint",
                            "evidence_reference_id": self.evidence_ref_id,
                        }
                    ],
                    "correlation_id": self.correlation_id,
                    "mutation": "none",
                },
            )

        return self._response(tool_request, {"unsupported_command": command}, status="unsupported")

    @staticmethod
    def _response(
        tool_request: dict[str, Any],
        result: dict[str, Any],
        *,
        status: str = "ok",
    ) -> dict[str, Any]:
        return {
            "request_id": tool_request["request_id"],
            "status": status,
            "result": result,
            "responded_at": "2026-06-29T00:00:00Z",
        }


@pytest.mark.asyncio
async def test_mcp_bot_advisory_cograph_golden_path_smoke(monkeypatch):
    daemon = _AdvisoryCographDaemon()
    monkeypatch.setattr(server, "_client", lambda: daemon)

    capture = await server.call_tool(
        "bicameral.capture_context",
        {
            "session_id": daemon.session_id,
            "correlation_id": daemon.correlation_id,
            "source_kind": "mcp_session",
            "source_link": "mcp://session/sess-580",
            "session_turns": [
                {
                    "role": "assistant",
                    "content": "Plan: add retry coverage around daemon reads.",
                }
            ],
            "tool_calls": [{"tool": "pytest", "request_id": "tool-580"}],
            "tool_outputs": [{"tool": "pytest", "output": "1 passed"}],
            "command_outputs": [{"command": "pytest -q", "stdout": "1 passed"}],
            "code_hints": [
                {
                    "file": "src/retry.py",
                    "range": "10:1-20:1",
                    "symbol": "RetryPolicy",
                }
            ],
            "code_region_hints": [{"path": "src/retry.py", "start_line": 10, "end_line": 20}],
            "evidence_references": [{"kind": "command_output", "id": "cmd-580"}],
            "metadata": {"workspace_session_id": daemon.session_id},
        },
    )
    capture_payload = json.loads(capture[0].text)

    preflight = await server.call_tool(
        "bicameral.preflight",
        {
            "files": ["src/retry.py"],
            "symbols": ["RetryPolicy"],
            "branch": "feat/580-advisory-cograph-smoke",
            "checkpoint_hint": "pre_work",
        },
    )
    preflight_payload = json.loads(preflight[0].text)

    search = await server.call_tool(
        "bicameral.search",
        {"query": "retry failing daemon reads", "scope": "trusted_corpus"},
    )
    search_payload = json.loads(search[0].text)

    history = await server.call_tool(
        "bicameral.history",
        {"decision_id": daemon.decision_id, "include_events": True},
    )
    history_payload = json.loads(history[0].text)

    inspect = await server.call_tool(
        "bicameral.binding.inspect",
        {"decision_or_candidate_id": daemon.candidate_id},
    )
    inspect_payload = json.loads(inspect[0].text)

    commands = [request["command"]["name"] for request in daemon.requests]
    assert commands == [
        "ingest.submit_local",
        "lookup.query",
        "preflight.run",
        "search.query",
        "history.list",
        "binding.inspect",
    ]

    ingest_params = daemon.requests[0]["command"]["params"]
    snapshot = json.loads(ingest_params["snapshot_content"])
    assert snapshot["kind"] == "SourceSnapshot"
    assert snapshot["source"]["source_type"] == "mcp_session"
    assert snapshot["evidence_references"] == [{"kind": "command_output", "id": "cmd-580"}]
    assert ingest_params["binding_hints"] == [
        {"file": "src/retry.py", "range": "10:1-20:1", "symbol": "RetryPolicy"}
    ]
    assert ingest_params["metadata"]["correlation_id"] == daemon.correlation_id
    assert ingest_params["metadata"]["mcp_session_id"] == daemon.session_id

    assert capture_payload["result"]["source_snapshot_id"] == daemon.source_snapshot_id
    assert capture_payload["result"]["evidence_reference_ids"] == [daemon.evidence_ref_id]
    assert {event["side"] for event in capture_payload["result"]["telemetry"]} == {"mcp", "bot"}
    assert capture_payload["result"]["correlation_id"] == daemon.correlation_id

    assert preflight_payload["stages"]["lookup"]["status"] == "completed"
    assert preflight_payload["stages"]["projection"]["status"] == "source_only"
    assert preflight_payload["result"]["result"]["mutation"] == "none"
    assert preflight_payload["result"]["result"]["evidence_reference_ids"] == [
        daemon.evidence_ref_id
    ]

    match = search_payload["matches"][0]
    assert match["source_link"] == "mcp://session/sess-580#command=pytest"
    assert match["snapshot_id"] == daemon.source_snapshot_id
    assert match["evidence_ref_id"] == daemon.evidence_ref_id
    assert match["authority"] == "source_only_advisory"

    assert history_payload["decisions"][0]["evidence_ref_id"] == daemon.evidence_ref_id
    assert history_payload["events"][0]["evidence_refs"] == [daemon.evidence_ref_id]
    assert inspect_payload["bindings"][0]["authority"] == "source_only_advisory"
    assert inspect_payload["bindings"][0]["graph_readiness"] == "source_only"

    rendered = json.dumps(
        [capture_payload, preflight_payload, search_payload, history_payload, inspect_payload]
    ).lower()
    assert "compliance_state" not in rendered
    assert "signoff_state" not in rendered
    assert "implementation_proof" not in rendered
    assert "safe_to_merge" not in rendered
    assert "merge_safe" not in rendered
    assert "safe to merge" not in rendered
