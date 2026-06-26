"""Tests for feature area brief narrative rendering.

Acceptance criteria:
- Timeline entries render chronologically with decision IDs, actors, and excerpts.
- Supersession relationships and drift status are visible in timeline and graph.
- Open items (drift, pending ratification) are rendered faithfully.
- Graph edges render source → relation → target without inference.
- Unknown scope and limitations are disclosed in footer.
- Empty brief payload returns a structured note, not a crash.
- MCP never infers compliance, safety, or completeness beyond daemon claims.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import server
from brief_renderer import format_brief_narrative
from tool_request import MCP_TOOL_COMMANDS
from version import TOOLREQUEST_PROTOCOL_VERSION

FIXTURES = Path(__file__).parent / "fixtures" / "toolresponses"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# format_brief_narrative — rendering fidelity
# ---------------------------------------------------------------------------


class TestBriefWithEntries:
    """Daemon returns a full brief with timeline, graph edges, and open items."""

    @pytest.fixture()
    def output(self):
        fixture = _load_fixture("brief.render.json")
        return format_brief_narrative(fixture).text

    def test_heading_contains_topic(self, output):
        assert "# Backport Strategy — Decision Context Brief" in output

    def test_heading_contains_generated_date(self, output):
        assert "Generated 2026-06-25" in output

    def test_heading_contains_decision_count(self, output):
        assert "3 decisions" in output

    def test_heading_contains_active_count(self, output):
        assert "2 active" in output

    def test_heading_contains_drifted_count(self, output):
        assert "1 drifted" in output

    def test_heading_contains_superseded_count(self, output):
        assert "1 superseded" in output

    def test_pending_ratification_omitted_when_zero(self, output):
        assert "pending ratification" not in output

    def test_timeline_section_present(self, output):
        assert "## Timeline" in output

    def test_timeline_entry_has_date(self, output):
        assert "[2026-06-10]" in output

    def test_timeline_entry_has_actor(self, output):
        assert "alice decided:" in output

    def test_timeline_entry_has_title(self, output):
        assert '"Require rebase for multi-commit backports"' in output

    def test_timeline_entry_has_decision_id(self, output):
        assert "(DEC-5)" in output

    def test_timeline_source_rendered(self, output):
        assert "Source: ADR-0001 (local://adr-0001.md)" in output

    def test_timeline_superseded_status(self, output):
        assert "superseded by DEC-12" in output

    def test_timeline_signoff_rendered(self, output):
        assert "signed by carol" in output
        assert "2026-06-18" in output

    def test_timeline_binding_rendered(self, output):
        assert "`backport_single_commit`" in output
        assert "lines 42-58" in output

    def test_timeline_freshness_visible(self, output):
        assert "**Drifted**" in output

    def test_timeline_excerpt_rendered(self, output):
        assert "*Cherry-pick is the canonical method" in output

    def test_open_items_section_present(self, output):
        assert "## Open Items" in output

    def test_open_item_drift_rendered(self, output):
        assert "**Drift Detected:**" in output
        assert "DEC-12" in output
        assert "release.py still uses rebase strategy" in output

    def test_graph_section_present(self, output):
        assert "## Decision Graph" in output

    def test_graph_supersession_edge(self, output):
        assert "DEC-12 —supersedes→ DEC-5" in output

    def test_graph_context_for_edge(self, output):
        assert "DEC-7 —context for→ DEC-12" in output

    def test_graph_drifted_by_edge(self, output):
        assert "DEC-12 —drifted by→ ev-drift-001" in output

    def test_footer_unknown_scope(self, output):
        assert "Unknown scope: bindings:graph_index" in output

    def test_footer_limitations(self, output):
        assert "Binding evidence projection not yet materialized" in output

    def test_no_strengthening_claims(self, output):
        text = output.lower()
        for forbidden in [
            "no conflict",
            "compliant",
            "safe",
            "globally complete",
            "all clear",
            "no issues",
        ]:
            assert forbidden not in text


class TestBriefEmpty:
    """Daemon returns a response with no brief payload."""

    @pytest.fixture()
    def output(self):
        response = {
            "request_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            "status": "ok",
            "unknown_scope": ["bindings:graph_index"],
            "limitations": ["Feature area not indexed"],
        }
        content = format_brief_narrative(response)
        return json.loads(content.text)

    def test_status_forwarded(self, output):
        assert output["status"] == "ok"

    def test_note_explains_empty_brief(self, output):
        assert "no brief data" in output["note"]

    def test_unknown_scope_visible(self, output):
        assert output["unknown_scope"] == ["bindings:graph_index"]

    def test_limitations_visible(self, output):
        assert output["limitations"] == ["Feature area not indexed"]


class TestBriefMinimalEntries:
    """Brief with a single entry and no optional fields."""

    @pytest.fixture()
    def output(self):
        response = {
            "request_id": "ffffffff-ffff-4fff-8fff-ffffffffffff",
            "status": "ok",
            "brief": {
                "topic": "Logging",
                "generated_at": "2026-06-25T12:00:00Z",
                "stats": {"total_decisions": 1, "active": 1},
                "entries": [
                    {
                        "date": "2026-06-20",
                        "decision_id": "DEC-99",
                        "title": "Use structured logging",
                    }
                ],
            },
        }
        return format_brief_narrative(response).text

    def test_heading_rendered(self, output):
        assert "# Logging — Decision Context Brief" in output

    def test_single_entry_rendered(self, output):
        assert "DEC-99" in output
        assert '"Use structured logging"' in output

    def test_open_items_section_omitted(self, output):
        assert "## Open Items" not in output

    def test_graph_section_omitted(self, output):
        assert "## Decision Graph" not in output

    def test_footer_omitted_when_no_scope_or_limitations(self, output):
        assert "Unknown scope" not in output
        assert "Limitations" not in output


class TestBriefNoTimeline:
    """Brief with graph edges but no timeline entries."""

    @pytest.fixture()
    def output(self):
        response = {
            "request_id": "11111111-1111-4111-8111-111111111111",
            "status": "ok",
            "brief": {
                "topic": "Auth",
                "generated_at": "2026-06-25T12:00:00Z",
                "stats": {"total_decisions": 0},
                "graph_edges": [
                    {
                        "source": "DEC-20",
                        "relation": "binds_to",
                        "target": "auth_handler",
                    }
                ],
            },
        }
        return format_brief_narrative(response).text

    def test_timeline_section_omitted(self, output):
        assert "## Timeline" not in output

    def test_graph_section_present(self, output):
        assert "## Decision Graph" in output
        assert "DEC-20 —binds to→ auth_handler" in output


# ---------------------------------------------------------------------------
# Server integration — brief routing
# ---------------------------------------------------------------------------


class _BriefFakeClient:
    """Fake daemon that returns a brief.render response."""

    def __init__(self, fixture_name: str):
        self._fixture = _load_fixture(fixture_name)
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
async def test_brief_tool_routes_to_narrative_formatter(monkeypatch):
    fake = _BriefFakeClient("brief.render.json")
    monkeypatch.setattr(server, "_client", lambda: fake)

    content = await server.call_tool("bicameral.brief", {"topic": "Backport Strategy"})
    text = content[0].text

    assert "# Backport Strategy — Decision Context Brief" in text
    assert "## Timeline" in text
    assert "## Decision Graph" in text


@pytest.mark.asyncio
async def test_brief_tool_sends_correct_command(monkeypatch):
    fake = _BriefFakeClient("brief.render.json")
    monkeypatch.setattr(server, "_client", lambda: fake)

    await server.call_tool("bicameral.brief", {"topic": "Backport Strategy"})

    assert len(fake.requests) == 1
    assert fake.requests[0]["command"]["name"] == "brief.render"
    assert fake.requests[0]["command"]["params"]["topic"] == "Backport Strategy"


@pytest.mark.asyncio
async def test_brief_empty_response_handled(monkeypatch):
    class _EmptyBriefClient:
        async def capabilities(self) -> dict:
            return {
                "toolrequest_protocol_version": TOOLREQUEST_PROTOCOL_VERSION,
                "supported_commands": list(MCP_TOOL_COMMANDS.values()),
            }

        async def send_tool_request(self, tool_request: dict) -> dict:
            return {
                "request_id": "empty-id",
                "status": "ok",
                "responded_at": "2026-06-25T00:00:00Z",
            }

    monkeypatch.setattr(server, "_client", lambda: _EmptyBriefClient())

    content = await server.call_tool("bicameral.brief", {"topic": "Unknown Area"})
    response = json.loads(content[0].text)

    assert response["status"] == "ok"
    assert "no brief data" in response["note"]
