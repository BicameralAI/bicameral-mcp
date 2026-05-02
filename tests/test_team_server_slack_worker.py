"""Functionality tests for team_server Phase 3 — Slack ingest worker."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SECRET_KEY", "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA=")


class _FakeSlackClient:
    """Minimal stand-in for slack_sdk.WebClient.conversations_history."""

    def __init__(self, messages_by_channel: dict[str, list[dict]]):
        self._messages = messages_by_channel
        self.calls: list[str] = []

    def conversations_history(self, channel: str, **kwargs):
        self.calls.append(channel)
        return {"messages": self._messages.get(channel, []), "ok": True}


@pytest.mark.asyncio
async def test_worker_polls_allowlisted_channels_only():
    """Behavior: poll_once invokes Slack's conversations_history only for
    channels in the allow-list, never for unlisted channels."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers.slack_worker import poll_once

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        slack = _FakeSlackClient({
            "C-ALLOW-1": [{"ts": "1.0", "text": "msg"}],
            "C-ALLOW-2": [],
            "C-DENY":     [{"ts": "2.0", "text": "should not be polled"}],
        })

        async def stub_extractor(text):
            return {"decisions": []}

        await poll_once(
            db_client=client,
            slack_client=slack,
            workspace_team_id="T1",
            channels=["C-ALLOW-1", "C-ALLOW-2"],
            extractor=stub_extractor,
        )
        assert set(slack.calls) == {"C-ALLOW-1", "C-ALLOW-2"}
        assert "C-DENY" not in slack.calls
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_worker_writes_team_event_for_each_message():
    """Behavior: feeding the worker N messages produces N team_event rows,
    each with author_email='team-server@<team_id>.bicameral' and
    event_type='ingest'."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers.slack_worker import poll_once

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        slack = _FakeSlackClient({
            "C1": [
                {"ts": "1.0", "text": "decision one"},
                {"ts": "2.0", "text": "decision two"},
                {"ts": "3.0", "text": "decision three"},
            ],
        })

        async def stub_extractor(text):
            return {"decisions": [text]}

        await poll_once(
            db_client=client,
            slack_client=slack,
            workspace_team_id="T9",
            channels=["C1"],
            extractor=stub_extractor,
        )
        rows = await client.query("SELECT * FROM team_event")
        assert len(rows) == 3
        for row in rows:
            assert row["author_email"] == "team-server@T9.bicameral"
            assert row["event_type"] == "ingest"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_worker_dedups_via_message_ts():
    """Behavior: feeding the same Slack message ts twice produces only one
    team_event row (idempotency via the canonical-extraction cache key)."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers.slack_worker import poll_once

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        slack = _FakeSlackClient({
            "C1": [{"ts": "100.0", "text": "same message"}],
        })

        async def stub_extractor(text):
            return {"decisions": [text]}

        for _ in range(2):
            await poll_once(
                db_client=client,
                slack_client=slack,
                workspace_team_id="T-DEDUP",
                channels=["C1"],
                extractor=stub_extractor,
            )
        rows = await client.query(
            "SELECT * FROM team_event WHERE author_email = 'team-server@T-DEDUP.bicameral'"
        )
        assert len(rows) == 1
    finally:
        await client.close()
