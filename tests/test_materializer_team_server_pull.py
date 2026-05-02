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
async def test_materializer_handles_team_server_unavailable_gracefully(monkeypatch, tmp_path, caplog):
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
