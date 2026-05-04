"""Functionality tests for team_server Phase 4 — HTTP /events API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SECRET_KEY", "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA=")


def _seed_events(client_test, n: int):
    """Seed N team_event rows via the events API by calling the
    canonical-extraction worker path through poll_once. For test simplicity
    we instead seed directly via the HTTP server's lifespan db handle."""
    # Use the test client's app state — the lifespan opened the DB.
    db = client_test.app.state.db

    async def _seed():
        from team_server.sync.peer_writer import write_team_event

        for i in range(n):
            await write_team_event(
                db.client,
                workspace_team_id="T-SEED",
                event_type="ingest",
                payload={"i": i},
            )

    import asyncio

    asyncio.get_event_loop().run_until_complete(_seed())


def test_get_events_returns_team_events_in_sequence_order():
    """Behavior: GET /events returns rows ordered by sequence ascending."""
    from team_server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        _seed_events(client, 5)
        resp = client.get("/events", params={"since": 0, "limit": 100})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 5
    sequences = [row["sequence"] for row in body]
    assert sequences == sorted(sequences)
    assert sequences[0] >= 1


def test_get_events_paginates_via_since_cursor():
    """Behavior: ?since=N returns only events with sequence > N."""
    from team_server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        _seed_events(client, 7)
        # First page
        first = client.get("/events", params={"since": 0, "limit": 3}).json()
        assert len(first) == 3
        last_seq = first[-1]["sequence"]
        # Second page from cursor
        second = client.get("/events", params={"since": last_seq, "limit": 100}).json()
        seqs_second = [r["sequence"] for r in second]
        assert all(s > last_seq for s in seqs_second)
        assert len(second) == 4


def test_get_events_returns_empty_when_no_new_events():
    """Behavior: ?since past-end returns empty list, not error."""
    from team_server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        _seed_events(client, 2)
        resp = client.get("/events", params={"since": 99999, "limit": 100})
    assert resp.status_code == 200
    assert resp.json() == []
