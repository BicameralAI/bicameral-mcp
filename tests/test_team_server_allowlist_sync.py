"""Phase 1 — channel_allowlist startup-time YAML→DB sync."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")


def _build_config(team_id: str, channels: list[str]):
    from team_server.config import (
        SlackConfig, TeamServerConfig, WorkspaceConfig,
    )
    return TeamServerConfig(slack=SlackConfig(
        workspaces=[WorkspaceConfig(team_id=team_id, channels=channels)],
    ))


@pytest.mark.asyncio
async def test_sync_inserts_channels_for_workspace_in_yaml():
    from team_server.auth.allowlist_sync import sync_channel_allowlist
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        rows = await client.query(
            "CREATE workspace CONTENT { name: 'W', slack_team_id: 'T1', "
            "oauth_token_encrypted: '' }"
        )
        config = _build_config("T1", ["C-A", "C-B"])
        await sync_channel_allowlist(client, config)
        rows = await client.query(
            "SELECT channel_id FROM channel_allowlist"
        )
        channel_ids = {r["channel_id"] for r in rows}
        assert channel_ids == {"C-A", "C-B"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sync_is_idempotent():
    from team_server.auth.allowlist_sync import sync_channel_allowlist
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await client.query(
            "CREATE workspace CONTENT { name: 'W', slack_team_id: 'T1', "
            "oauth_token_encrypted: '' }"
        )
        config = _build_config("T1", ["C-A", "C-B"])
        await sync_channel_allowlist(client, config)
        await sync_channel_allowlist(client, config)
        rows = await client.query("SELECT * FROM channel_allowlist")
        assert len(rows) == 2
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sync_skips_workspaces_not_in_yaml():
    from team_server.auth.allowlist_sync import sync_channel_allowlist
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await client.query(
            "CREATE workspace CONTENT { name: 'T1', slack_team_id: 'T1', "
            "oauth_token_encrypted: '' }"
        )
        await client.query(
            "CREATE workspace CONTENT { name: 'T2', slack_team_id: 'T2', "
            "oauth_token_encrypted: '' }"
        )
        # YAML mentions T1 only
        config = _build_config("T1", ["C-A"])
        await sync_channel_allowlist(client, config)
        # T2 should have no allowlist rows
        t2_rows = await client.query(
            "SELECT * FROM channel_allowlist "
            "WHERE workspace_id = (SELECT VALUE id FROM workspace "
            "WHERE slack_team_id = 'T2')[0]"
        )
        assert len(t2_rows) == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sync_skips_workspaces_not_in_db():
    """YAML mentions T-MISSING but no matching workspace row exists.
    Sync logs and continues; no orphan workspace_id rows are created."""
    from team_server.auth.allowlist_sync import sync_channel_allowlist
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        config = _build_config("T-MISSING", ["C-X"])
        await sync_channel_allowlist(client, config)
        rows = await client.query("SELECT * FROM channel_allowlist")
        assert len(rows) == 0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_sync_removes_channels_not_in_yaml():
    """Operator removes a channel from YAML by editing it out; sync
    deletes the corresponding allowlist row on next run."""
    from team_server.auth.allowlist_sync import sync_channel_allowlist
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await client.query(
            "CREATE workspace CONTENT { name: 'W', slack_team_id: 'T1', "
            "oauth_token_encrypted: '' }"
        )
        config_full = _build_config("T1", ["C-A", "C-B"])
        await sync_channel_allowlist(client, config_full)
        config_reduced = _build_config("T1", ["C-A"])
        await sync_channel_allowlist(client, config_reduced)
        rows = await client.query(
            "SELECT channel_id FROM channel_allowlist"
        )
        channel_ids = {r["channel_id"] for r in rows}
        assert channel_ids == {"C-A"}
    finally:
        await client.close()
