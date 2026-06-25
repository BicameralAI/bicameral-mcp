"""Tests for RecallPacket rendering fidelity (mcp#638).

Acceptance criteria:
- Render searched scope, unknown scope, matches, evidence refs,
  freshness/readiness labels, and allowed next actions.
- No-match output says lookup found no relevant items only within searched scope.
- Unknown scope is not hidden or summarized away.
- Stale / source_only / candidate labels remain visible.
- Never infer no-conflict, compliance, safety, or global completeness from
  narrow scope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server
from responses import format_recall_packet
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "toolresponses"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# format_recall_packet — rendering fidelity
# ---------------------------------------------------------------------------


class TestRecallPacketWithMatches:
    """Daemon returns matches with evidence refs and freshness labels."""

    @pytest.fixture()
    def output(self):
        fixture = _load_fixture("recall_packet_with_matches.json")
        content = format_recall_packet(fixture)
        return json.loads(content.text)

    def test_status_forwarded(self, output):
        assert output["status"] == "ok"

    def test_searched_scope_rendered(self, output):
        assert output["searched_scope"] == [
            "decisions:local_ledger",
            "candidates:local_ledger",
        ]

    def test_unknown_scope_not_hidden(self, output):
        assert output["unknown_scope"] == [
            "bindings:graph_index",
            "decisions:team_remote",
        ]

    def test_matches_preserve_kind_and_id(self, output):
        assert len(output["matches"]) == 2
        assert output["matches"][0]["kind"] == "decision"
        assert output["matches"][0]["id"] == "DEC-12"
        assert output["matches"][1]["kind"] == "candidate"
        assert output["matches"][1]["id"] == "CAND-5"

    def test_evidence_refs_visible(self, output):
        assert output["matches"][0]["evidence_refs"] == ["ev-001", "ev-002"]
        assert output["matches"][1]["evidence_refs"] == ["ev-003"]

    def test_freshness_labels_visible(self, output):
        assert output["matches"][0]["freshness"] == "stale"
        assert output["matches"][1]["freshness"] == "current"

    def test_readiness_labels_visible(self, output):
        assert output["matches"][0]["readiness"] == "accepted"
        assert output["matches"][1]["readiness"] == "source_only"

    def test_source_link_preserved(self, output):
        assert output["matches"][0]["source_link"] == "local://adr-0003.md"

    def test_excerpt_preserved(self, output):
        assert "structured logging" in output["matches"][0]["excerpt"]

    def test_allowed_next_actions_forwarded(self, output):
        assert output["allowed_next_actions"] == ["bind", "inspect", "expand_scope"]

    def test_expand_scope_forwarded(self, output):
        assert "expand_scope" in output
        assert output["expand_scope"]["available"] == [
            "bindings:graph_index",
            "decisions:team_remote",
        ]

    def test_no_match_note_absent_when_matches_exist(self, output):
        assert "no_match_note" not in output

    def test_no_strengthening_claims_in_output(self, output):
        text = json.dumps(output)
        for forbidden in [
            "no conflict",
            "compliant",
            "safe",
            "globally complete",
            "all clear",
            "no issues",
        ]:
            assert forbidden not in text.lower()


class TestRecallPacketNoMatches:
    """Daemon returns zero matches — MCP must not imply global absence."""

    @pytest.fixture()
    def output(self):
        fixture = _load_fixture("recall_packet_no_matches.json")
        content = format_recall_packet(fixture)
        return json.loads(content.text)

    def test_no_match_note_present(self, output):
        assert "no_match_note" in output

    def test_no_match_note_scoped_language(self, output):
        note = output["no_match_note"]
        assert "within searched scope" in note
        assert "does not imply absence outside" in note

    def test_no_match_note_includes_scope_description(self, output):
        note = output["no_match_note"]
        assert "decisions:local_ledger" in note

    def test_unknown_scope_visible(self, output):
        assert len(output["unknown_scope"]) == 3
        assert "bindings:graph_index" in output["unknown_scope"]

    def test_expand_scope_present(self, output):
        assert "expand_scope" in output
        assert "candidates:local_ledger" in output["expand_scope"]["available"]

    def test_matches_is_empty_list(self, output):
        assert output["matches"] == []

    def test_no_strengthening_from_empty_matches(self, output):
        text = json.dumps(output)
        for forbidden in [
            "no conflict",
            "compliant",
            "safe",
            "no issues found",
            "all clear",
            "complete",
        ]:
            assert forbidden not in text.lower()


class TestRecallPacketPartialScope:
    """Daemon searched partial scope with unknown bindings:graph_index."""

    @pytest.fixture()
    def output(self):
        fixture = _load_fixture("recall_packet_partial_scope.json")
        content = format_recall_packet(fixture)
        return json.loads(content.text)

    def test_stale_decision_freshness_visible(self, output):
        assert output["matches"][0]["freshness"] == "stale"
        assert output["matches"][0]["readiness"] == "accepted"

    def test_unknown_scope_disclosed(self, output):
        assert output["unknown_scope"] == ["bindings:graph_index"]

    def test_no_expand_scope_when_absent(self, output):
        assert "expand_scope" not in output

    def test_allowed_next_actions(self, output):
        assert output["allowed_next_actions"] == ["bind", "inspect"]


class TestRecallPacketSourceOnlyCandidate:
    """Daemon returns a source_only candidate — label must remain visible."""

    @pytest.fixture()
    def output(self):
        fixture = _load_fixture("recall_packet_source_only_candidate.json")
        content = format_recall_packet(fixture)
        return json.loads(content.text)

    def test_source_only_readiness_visible(self, output):
        assert output["matches"][0]["readiness"] == "source_only"
        assert output["matches"][0]["kind"] == "candidate"

    def test_candidate_id_preserved(self, output):
        assert output["matches"][0]["id"] == "CAND-9"

    def test_evidence_refs_preserved(self, output):
        assert output["matches"][0]["evidence_refs"] == ["ev-020", "ev-021"]

    def test_empty_unknown_scope_still_rendered(self, output):
        assert output["unknown_scope"] == []

    def test_review_actions_forwarded(self, output):
        assert "review.accept_candidate" in output["allowed_next_actions"]
        assert "review.reject_candidate" in output["allowed_next_actions"]


# ---------------------------------------------------------------------------
# Server integration — recall_packet routing
# ---------------------------------------------------------------------------


class _RecallPacketFakeClient:
    """Fake daemon that returns a recall_packet in search responses."""

    def __init__(self, recall_fixture_name: str):
        self._fixture = _load_fixture(recall_fixture_name)
        self.requests: list[dict] = []

    async def capabilities(self) -> dict:
        return {
            "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
            "supported_commands": list(MCP_TOOL_COMMANDS.values()),
        }

    async def send_tool_request(self, tool_request: dict) -> dict:
        self.requests.append(tool_request)
        return self._fixture


@pytest.mark.asyncio
async def test_search_with_recall_packet_uses_dedicated_formatter(monkeypatch):
    fake = _RecallPacketFakeClient("recall_packet_with_matches.json")
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.search", {"query": "logging"})
    response = json.loads(content[0].text)

    assert "searched_scope" in response
    assert "unknown_scope" in response
    assert "matches" in response
    assert len(response["matches"]) == 2


@pytest.mark.asyncio
async def test_search_without_recall_packet_uses_generic_formatter(monkeypatch):
    """Backward-compat: search responses without recall_packet use generic fmt."""

    class _LegacySearchClient:
        async def capabilities(self) -> dict:
            return {
                "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
                "supported_commands": list(MCP_TOOL_COMMANDS.values()),
            }

        async def send_tool_request(self, tool_request: dict) -> dict:
            return {
                "request_id": "legacy-id",
                "status": "ok",
                "result": {"matches": [{"kind": "decision", "decision_id": "DEC-1"}]},
                "responded_at": "2026-06-24T00:00:00Z",
            }

    monkeypatch.setattr(server, "_client", lambda: _LegacySearchClient())

    content = await server.call_tool("bicameral.search", {"query": "x"})
    response = json.loads(content[0].text)

    assert "searched_scope" not in response
    assert response["status"] == "ok"


@pytest.mark.asyncio
async def test_non_search_tool_ignores_recall_packet_key(monkeypatch):
    """If a non-search response somehow contains recall_packet, still route it."""

    class _HistoryWithRecallClient:
        async def capabilities(self) -> dict:
            return {
                "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
                "supported_commands": list(MCP_TOOL_COMMANDS.values()),
            }

        async def send_tool_request(self, tool_request: dict) -> dict:
            return {
                "request_id": "hist-id",
                "status": "ok",
                "recall_packet": {
                    "searched_scope": ["decisions:local_ledger"],
                    "unknown_scope": [],
                    "matches": [],
                },
                "responded_at": "2026-06-24T00:00:00Z",
            }

    monkeypatch.setattr(server, "_client", lambda: _HistoryWithRecallClient())

    content = await server.call_tool("bicameral.history", {})
    response = json.loads(content[0].text)

    assert "searched_scope" in response
    assert "no_match_note" in response
