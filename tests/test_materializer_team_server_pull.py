"""Functionality tests for team_server Phase 4 — EventMaterializer extension
that pulls events from a team-server URL."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.mark.asyncio
async def test_materializer_pulls_from_team_server_url(monkeypatch, tmp_path):
    """Behavior: when team_server_url is set, replay() invokes a GET /events
    on the URL and processes the returned events."""
    from events.team_server_pull import pull_team_server_events

    captured: dict = {}

    async def fake_get(self, url, params, timeout):
        captured["url"] = url
        captured["params"] = params
        request = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json=[
                {"sequence": 1, "author_email": "a@b", "event_type": "ingest", "payload": {}},
                {"sequence": 2, "author_email": "a@b", "event_type": "ingest", "payload": {}},
            ],
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    watermark = tmp_path / "team_server_watermark"
    events = await pull_team_server_events(
        team_server_url="http://team:8765",
        watermark_path=watermark,
    )
    assert captured["url"] == "http://team:8765/events"
    assert captured["params"]["since"] == 0
    assert len(events) == 2
    # Watermark advanced
    assert watermark.read_text(encoding="utf-8").strip() == "2"


@pytest.mark.asyncio
async def test_materializer_persists_team_server_watermark_separately(monkeypatch, tmp_path):
    """Behavior: second invocation passes since=<previous-watermark>."""
    from events.team_server_pull import pull_team_server_events

    seen_since: list[int] = []

    async def fake_get(self, url, params, timeout):
        seen_since.append(params["since"])
        # First call: return events 1..3; subsequent calls: empty
        request = httpx.Request("GET", url)
        if params["since"] == 0:
            return httpx.Response(
                200,
                json=[
                    {"sequence": 1, "author_email": "a", "event_type": "i", "payload": {}},
                    {"sequence": 2, "author_email": "a", "event_type": "i", "payload": {}},
                    {"sequence": 3, "author_email": "a", "event_type": "i", "payload": {}},
                ],
                request=request,
            )
        return httpx.Response(200, json=[], request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    watermark = tmp_path / "team_server_watermark"
    await pull_team_server_events(team_server_url="http://team:8765", watermark_path=watermark)
    await pull_team_server_events(team_server_url="http://team:8765", watermark_path=watermark)
    assert seen_since == [0, 3]


@pytest.mark.asyncio
async def test_materializer_handles_team_server_unavailable_gracefully(
    monkeypatch, tmp_path, caplog
):
    """Behavior: 503 from team-server does NOT raise; returns empty events;
    watermark unchanged. Failure-isolation contract per audit (research F3
    — outside the deterministic core)."""
    from events.team_server_pull import pull_team_server_events

    async def fake_get(self, url, params, timeout):
        raise httpx.ConnectError("team-server unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    watermark = tmp_path / "team_server_watermark"
    # Pre-populate watermark to verify it's unchanged
    watermark.write_text("42", encoding="utf-8")
    events = await pull_team_server_events(
        team_server_url="http://team:8765",
        watermark_path=watermark,
    )
    assert events == []
    # Watermark unchanged
    assert watermark.read_text(encoding="utf-8").strip() == "42"


# ── Phase 2 (v0-release-blockers): materializer bridges team-server events ──


import json as _json
from pathlib import Path as _Path


class _RecordingInnerAdapter:
    def __init__(self):
        self.calls: list[dict] = []

    async def connect(self):
        return None

    async def ingest_payload(self, payload, ctx=None):
        self.calls.append(payload)
        return {}


async def _materialize_one_event(tmp_path, event: dict) -> _RecordingInnerAdapter:
    """Helper: write a single JSONL event to events_dir, run replay,
    return the recording adapter to assert on."""
    from events.materializer import EventMaterializer

    events_dir = tmp_path / "events"
    local_dir = tmp_path / "local"
    events_dir.mkdir()
    local_dir.mkdir()
    jsonl = events_dir / "team-server@notion.bicameral.jsonl"
    jsonl.write_text(_json.dumps(event) + "\n", encoding="utf-8")
    materializer = EventMaterializer(events_dir, local_dir)
    inner = _RecordingInnerAdapter()
    await materializer.replay_new_events(inner)
    return inner


@pytest.mark.asyncio
async def test_materializer_dispatches_team_server_ingest_event(tmp_path):
    """Behavior: a JSONL line with event_type='ingest' and a team-server-
    shaped payload routes through the bridge to inner_adapter.ingest_payload."""
    event = {
        "sequence": 1,
        "author_email": "team-server@notion.bicameral",
        "event_type": "ingest",
        "payload": {
            "source_type": "slack",
            "source_ref": "C1/123.0",
            "content_hash": "h",
            "extraction": {
                "decisions": [
                    {"summary": "use REST", "context_snippet": "we decided to use REST"},
                ],
            },
        },
    }
    inner = await _materialize_one_event(tmp_path, event)
    assert len(inner.calls) == 1
    assert inner.calls[0]["source"] == "slack"


@pytest.mark.asyncio
async def test_materializer_bridges_slack_extraction_to_ingest_payload(tmp_path):
    event = {
        "sequence": 1,
        "author_email": "team-server@notion.bicameral",
        "event_type": "ingest",
        "payload": {
            "source_type": "slack",
            "source_ref": "C1/2.0",
            "content_hash": "h",
            "extraction": {
                "decisions": [
                    {"summary": "use REST", "context_snippet": "we decided to use REST"},
                ]
            },
        },
    }
    inner = await _materialize_one_event(tmp_path, event)
    assert inner.calls[0] == {
        "source": "slack",
        "repo": "",
        "commit_hash": "",
        "decisions": [{"description": "use REST", "source_excerpt": "we decided to use REST"}],
        "title": "C1/2.0",
    }


@pytest.mark.asyncio
async def test_materializer_bridges_notion_extraction_with_correct_source_type(tmp_path):
    """notion_database_row source_type normalizes to 'notion' on the
    bridged IngestPayload."""
    event = {
        "sequence": 1,
        "author_email": "team-server@notion.bicameral",
        "event_type": "ingest",
        "payload": {
            "source_type": "notion_database_row",
            "source_ref": "db1/page1",
            "content_hash": "h",
            "extraction": {
                "decisions": [
                    {"summary": "approved", "context_snippet": "approved by lead"},
                ]
            },
        },
    }
    inner = await _materialize_one_event(tmp_path, event)
    assert inner.calls[0]["source"] == "notion"


@pytest.mark.asyncio
async def test_materializer_skips_team_server_event_with_empty_decisions(tmp_path):
    event = {
        "sequence": 1,
        "author_email": "team-server@notion.bicameral",
        "event_type": "ingest",
        "payload": {
            "source_type": "slack",
            "source_ref": "C1/3.0",
            "content_hash": "h",
            "extraction": {"decisions": []},
        },
    }
    inner = await _materialize_one_event(tmp_path, event)
    assert inner.calls == []


@pytest.mark.asyncio
async def test_materializer_still_handles_legacy_ingest_completed_event_type(tmp_path):
    """Pre-existing v0 callers emit event_type='ingest.completed' with a
    CodeLocatorPayload-shaped payload (NOT team-server-shaped). The
    bridge's is_team_server_payload predicate returns False → original
    dispatch handles it."""
    event = {
        "sequence": 1,
        "author_email": "dev@example.com",
        "event_type": "ingest.completed",
        "payload": {
            # CodeLocatorPayload shape — has 'repo' and 'commit_hash'
            # but NO 'extraction' key (the team-server signature)
            "repo": "/tmp/repo",
            "commit_hash": "abc",
            "decisions": [{"description": "X"}],
        },
    }
    inner = await _materialize_one_event(tmp_path, event)
    assert len(inner.calls) == 1
    # The legacy payload reaches inner.ingest_payload UNCHANGED (not bridged)
    assert "repo" in inner.calls[0]
    assert inner.calls[0]["repo"] == "/tmp/repo"


@pytest.mark.asyncio
async def test_materializer_skips_team_server_event_with_malformed_payload(tmp_path):
    """Payload missing the 'extraction' key is not a team-server payload;
    nor does it match CodeLocatorPayload shape (no 'repo'/'commit_hash'
    in the meaningful sense). The materializer just no-ops with this
    shape. Functionality — exercises defensive shape-checking."""
    event = {
        "sequence": 1,
        "author_email": "team-server@notion.bicameral",
        "event_type": "ingest",
        "payload": {
            "source_type": "slack",
            "source_ref": "C1/malformed",
            # NO 'extraction' key — fails is_team_server_payload check
        },
    }
    inner = await _materialize_one_event(tmp_path, event)
    # Bridge predicate returned False; we then fall through to the legacy
    # 'ingest.completed' path which does NOT match etype='ingest', so no
    # ingest happens at all. inner.calls is empty.
    assert inner.calls == []
