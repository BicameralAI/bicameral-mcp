"""Review workflow surfacing for trusted-corpus proposals and contradictions."""

from __future__ import annotations

import json
from typing import Any

import pytest

import server
from governance_surface import format_governance_inbox, format_governance_resolve
from responses import format_review_queue_response
from tool_request import MCP_TOOL_COMMANDS, SUPPORTED_COMMANDS
from tool_schemas import tool_for_name
from version import TOOLREQUEST_PROTOCOL_VERSION


class _ReviewDaemon:
    def __init__(
        self, *, commands: list[str] | None = None, response: dict[str, Any] | None = None
    ):
        self.commands = commands if commands is not None else list(SUPPORTED_COMMANDS)
        self.response = response or {"status": "ok", "result": {}}
        self.requests: list[dict[str, Any]] = []

    async def capabilities(self) -> dict[str, Any]:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": self.commands,
        }

    async def send_tool_request(self, tool_request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(tool_request)
        return {"request_id": tool_request["request_id"], **self.response}


def test_review_tools_are_exposed_with_review_specific_names():
    for name in (
        "bicameral.review.candidates",
        "bicameral.review.corpus_proposals",
        "bicameral.review.promote_candidate",
        "bicameral.review.request_corpus_change",
        "bicameral.review.contradictions",
        "bicameral.review.triage_contradiction",
    ):
        assert tool_for_name(name) is not None


@pytest.mark.asyncio
async def test_candidate_listing_defaults_to_candidate_search(monkeypatch):
    daemon = _ReviewDaemon(
        response={
            "status": "ok",
            "result": {
                "matches": [
                    {
                        "kind": "candidate",
                        "candidate_id": "cand-614",
                        "title": "Checkout retry policy",
                        "review_state": "proposed",
                        "authority": "candidate",
                        "evidence_refs": ["ev-1"],
                        "source_refs": ["src-1"],
                        "provenance": {"provider": "linear", "source_id": "LIN-7"},
                        "affected_surface": {"feature_area": "checkout"},
                        "rationale": "New source evidence affects checkout retries.",
                        "allowed_actions": ["review.accept_candidate", "review.reject_candidate"],
                    }
                ]
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.review.candidates", {"limit": 5})
    rendered = json.loads(content[0].text)

    request = daemon.requests[0]
    assert MCP_TOOL_COMMANDS["bicameral.review.candidates"] == "search.query"
    assert request["command"]["name"] == "search.query"
    assert request["command"]["params"]["query"] == ""
    assert request["command"]["params"]["scope"] == "candidates"
    item = rendered["decision_candidates"][0]
    assert item["evidence_refs"] == ["ev-1"]
    assert item["source_refs"] == ["src-1"]
    assert item["provenance"]["provider"] == "linear"
    assert item["affected_surface"] == {"feature_area": "checkout"}
    assert item["rationale"] == "New source evidence affects checkout retries."


@pytest.mark.asyncio
async def test_corpus_proposals_request_correction_findings(monkeypatch):
    daemon = _ReviewDaemon(
        response={
            "status": "ok",
            "correction_findings_packet": {
                "packet_id": "pkt-614",
                "findings": [
                    {
                        "finding_id": "cf-614",
                        "summary": "Source doc may need update.",
                        "trusted_corpus_ref": {"kind": "decision", "id": "DEC-7"},
                        "source_doc_ref": {"provider": "gdrive", "doc_id": "doc-7"},
                        "affected_code_region": {"path": "src/checkout.py"},
                        "evidence_refs": ["ev-2"],
                        "suggested_action": "request_corpus_change",
                        "review_state": "review_needed",
                    }
                ],
                "review_handoff": {"target_tool": "bicameral.review.request_corpus_change"},
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool("bicameral.review.corpus_proposals", {"pr": "7"})
    rendered = json.loads(content[0].text)

    request = daemon.requests[0]
    assert request["command"]["name"] == "lookup.query"
    assert request["command"]["params"]["scope"] == "correction_capture"
    assert request["command"]["params"]["include_correction_findings"] is True
    finding = rendered["correction_findings_packet"]["findings"][0]
    assert finding["trusted_corpus_ref"]["id"] == "DEC-7"
    assert finding["source_doc_ref"]["provider"] == "gdrive"
    assert finding["evidence_refs"] == ["ev-2"]


@pytest.mark.asyncio
async def test_recall_review_actions_map_to_current_bot_commands(monkeypatch):
    daemon = _ReviewDaemon(response={"status": "ok", "result": {"outcome": "accepted"}})
    monkeypatch.setattr(server, "_client", lambda: daemon)

    approval_proof = {"kind": "signoff_token", "value": "tok-1", "actor_id": "owner-1"}
    await server.call_tool(
        "bicameral.review.promote_candidate",
        {
            "packet_id": "00000000-0000-0000-0000-000000000001",
            "candidate_id": "00000000-0000-0000-0000-000000000002",
            "promotion_outcome": "new_constraint",
            "approval_proof": approval_proof,
        },
    )
    await server.call_tool(
        "bicameral.review.request_corpus_change",
        {
            "packet_id": "00000000-0000-0000-0000-000000000001",
            "selected_item_ids": ["00000000-0000-0000-0000-000000000003"],
            "correction_kind": "source_contradiction",
            "rationale": "Corpus source conflicts with reviewed policy.",
            "approval_proof": approval_proof,
        },
    )

    assert daemon.requests[0]["command"]["name"] == "recall.promote_decision_candidate"
    assert daemon.requests[1]["command"]["name"] == "recall.request_correction"
    assert daemon.requests[1]["command"]["params"]["approval_proof"] == approval_proof


@pytest.mark.asyncio
async def test_candidate_promotion_initial_request_does_not_require_approval_proof(monkeypatch):
    daemon = _ReviewDaemon(
        response={
            "status": "ok",
            "result": {
                "outcome": "confirmation_required",
                "confirmation_required": {
                    "challenge_id": "chal-735",
                    "expires_at": "2026-07-16T03:45:00Z",
                    "binding": {
                        "packet_id": "pkt-735",
                        "candidate_id": "cand-735",
                        "outcome": "new_constraint",
                    },
                    "token": "secret-token-must-not-render",
                },
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool(
        "bicameral.review.promote_candidate",
        {
            "packet_id": "pkt-735",
            "candidate_id": "cand-735",
            "promotion_outcome": "new_constraint",
        },
    )
    rendered = json.loads(content[0].text)

    request = daemon.requests[0]
    assert request["command"]["name"] == "recall.promote_decision_candidate"
    assert "approval_proof" not in request["command"]["params"]
    assert "confirmation" not in request["command"]["params"]
    assert rendered["status"] == "confirmation_required"
    assert rendered["canonical_transition_materialized"] is False
    assert rendered["human_confirmation_required"] is True
    assert rendered["confirmation_required"]["token"] == "[REDACTED]"
    assert "secret-token-must-not-render" not in content[0].text


@pytest.mark.asyncio
async def test_candidate_promotion_confirmation_is_passed_through(monkeypatch):
    daemon = _ReviewDaemon(
        response={
            "status": "ok",
            "result": {
                "outcome": "promoted",
                "candidate_id": "cand-735",
                "decision_id": "DEC-735",
                "lineage": {"supersedes_decision_id": "DEC-1"},
            },
        }
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    confirmation = {
        "challenge_id": "chal-735",
        "secret": "human-confirmed-secret",
        "confirmed": True,
    }
    content = await server.call_tool(
        "bicameral.review.promote_candidate",
        {
            "packet_id": "pkt-735",
            "candidate_id": "cand-735",
            "promotion_outcome": "supersedes",
            "supersedes_decision_id": "DEC-1",
            "confirmation": confirmation,
        },
    )
    rendered = json.loads(content[0].text)

    request = daemon.requests[0]
    assert request["command"]["params"]["confirmation"] == confirmation
    assert rendered["review_result"]["candidate_id"] == "cand-735"
    assert rendered["review_result"]["decision_id"] == "DEC-735"
    assert "canonical_result_note" in rendered


def test_candidate_promotion_renderer_preserves_informed_confirmation_fields():
    rendered = json.loads(
        format_review_queue_response(
            {
                "request_id": "req-735",
                "status": "ok",
                "result": {
                    "matches": [
                        {
                            "kind": "candidate",
                            "candidate_id": "cand-735",
                            "title": "Checkout retry policy",
                            "excerpt": "Retries must be capped.",
                            "evidence_refs": ["ev-735"],
                            "source_refs": ["src-735"],
                            "relevance_reason": "Touches retry behavior.",
                            "readiness": "reviewable",
                            "freshness": "current",
                            "ambiguity": "low",
                            "related_decisions": ["DEC-1"],
                            "authority_required": "product_owner",
                            "proposed_outcome": "keeps_both_with_scope",
                            "scoping_effect": "applies only to checkout retries",
                            "challenge_expires_at": "2026-07-16T03:45:00Z",
                        }
                    ]
                },
            },
            item_key="decision_candidates",
        ).text
    )

    item = rendered["decision_candidates"][0]
    assert item["candidate_id"] == "cand-735"
    assert item["excerpt"] == "Retries must be capped."
    assert item["relevance_reason"] == "Touches retry behavior."
    assert item["readiness"] == "reviewable"
    assert item["freshness"] == "current"
    assert item["ambiguity"] == "low"
    assert item["related_decisions"] == ["DEC-1"]
    assert item["authority_required"] == "product_owner"
    assert item["proposed_outcome"] == "keeps_both_with_scope"
    assert item["scoping_effect"] == "applies only to checkout retries"


@pytest.mark.asyncio
async def test_recall_review_actions_are_capability_gated(monkeypatch):
    daemon = _ReviewDaemon(
        commands=[
            command for command in SUPPORTED_COMMANDS if command != "recall.request_correction"
        ]
    )
    monkeypatch.setattr(server, "_client", lambda: daemon)

    content = await server.call_tool(
        "bicameral.review.request_corpus_change",
        {
            "packet_id": "00000000-0000-0000-0000-000000000001",
            "selected_item_ids": ["00000000-0000-0000-0000-000000000003"],
            "correction_kind": "source_contradiction",
            "rationale": "Needs review.",
            "approval_proof": {"kind": "signoff_token", "value": "tok-1", "actor_id": "owner-1"},
        },
    )
    rendered = json.loads(content[0].text)

    assert rendered["status"] == "error"
    assert rendered["error_code"] == "daemon_capability_error"
    assert daemon.requests == []


def test_review_renderer_preserves_source_and_provenance_fields():
    rendered = json.loads(
        format_review_queue_response(
            {
                "request_id": "req-614",
                "status": "ok",
                "result": {
                    "matches": [
                        {
                            "kind": "candidate",
                            "candidate_id": "cand-1",
                            "evidence_refs": ["ev-1"],
                            "source_refs": ["src-1"],
                            "provenance": {"provider": "github"},
                            "affected_surface": {"path": "src/a.py"},
                            "rationale": "Reviewed source changed.",
                            "allowed_actions": ["review.accept_candidate"],
                        }
                    ]
                },
            },
            item_key="decision_candidates",
        ).text
    )

    item = rendered["decision_candidates"][0]
    assert item["evidence_refs"] == ["ev-1"]
    assert item["source_refs"] == ["src-1"]
    assert item["provenance"] == {"provider": "github"}
    assert item["affected_surface"] == {"path": "src/a.py"}
    assert item["rationale"] == "Reviewed source changed."


def test_governance_review_aliases_preserve_triage_payload_fields():
    inbox = json.loads(
        format_governance_inbox(
            {
                "request_id": "req-inbox",
                "status": "ok",
                "findings": [
                    {
                        "report_id": "CR-1",
                        "status": "open",
                        "triage_state": "needs_owner",
                        "affected_surface": {"feature_area": "checkout"},
                        "evidence_refs": ["ev-1"],
                        "source_refs": ["src-1"],
                        "provenance": {"provider": "linear"},
                        "rationale": "Ticket contradicts Decision DEC-7.",
                        "allowed_actions": ["acknowledge"],
                    }
                ],
            }
        ).text
    )
    result = json.loads(
        format_governance_resolve(
            {
                "request_id": "req-resolve",
                "status": "ok",
                "result": {
                    "report_id": "CR-1",
                    "action": "acknowledge",
                    "triage_state": "acknowledged",
                    "accepted": True,
                },
            }
        ).text
    )

    finding = inbox["findings"][0]
    assert finding["triage_state"] == "needs_owner"
    assert finding["source_refs"] == ["src-1"]
    assert finding["provenance"] == {"provider": "linear"}
    assert finding["affected_surface"] == {"feature_area": "checkout"}
    assert finding["rationale"] == "Ticket contradicts Decision DEC-7."
    assert result["triage_state"] == "acknowledged"
