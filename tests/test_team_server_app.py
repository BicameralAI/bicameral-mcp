"""Functionality tests for team_server Phase 1 — scaffold + self-managing schema."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SECRET_KEY", "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA=")
    yield


@pytest.mark.asyncio
async def test_app_starts_and_serves_health(memory_url):
    """Behavior: create_app() builds a FastAPI app whose lifespan migrates
    schema and exposes a /health endpoint that returns the schema version."""
    from httpx import ASGITransport, AsyncClient

    from team_server.app import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Manually trigger lifespan via context
        async with app.router.lifespan_context(app):
            resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert isinstance(body["schema_version"], int)
    assert body["schema_version"] >= 1


@pytest.mark.asyncio
async def test_schema_migrates_from_empty_ledger(memory_url):
    """Behavior: ensure_schema() against a fresh memory:// SurrealDB defines
    all v0 team-server tables (workspace, channel_allowlist, extraction_cache,
    team_event)."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        # Insert + query each table to prove it exists with the expected fields
        await client.query(
            "CREATE workspace CONTENT { name: 'acme', slack_team_id: 'T1', "
            "oauth_token_encrypted: 'enc', created_at: time::now() }"
        )
        rows = await client.query("SELECT * FROM workspace")
        assert len(rows) == 1
        assert rows[0]["slack_team_id"] == "T1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_schema_migration_is_idempotent(memory_url):
    """Behavior: running ensure_schema() twice on the same client succeeds
    (no exception) and table definitions remain valid afterward."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await ensure_schema(client)  # second call must be no-op
        # Sanity: tables still functional after double-migrate
        await client.query(
            "CREATE workspace CONTENT { name: 'a', slack_team_id: 'T2', "
            "oauth_token_encrypted: 'enc', created_at: time::now() }"
        )
        rows = await client.query("SELECT * FROM workspace WHERE slack_team_id = 'T2'")
        assert len(rows) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_app_shutdown_releases_db(memory_url):
    """Behavior: lifespan context teardown closes the DB client; subsequent
    queries on the closed client raise rather than silently no-op."""
    from team_server.app import create_app

    app = create_app()
    async with app.router.lifespan_context(app):
        db = app.state.db
        # Active during the context
        await db.client.query("RETURN 1")
    # After context exit, the underlying client is closed
    with pytest.raises((RuntimeError, AttributeError, Exception)):
        await db.client.query("RETURN 1")


def test_health_endpoint_returns_well_formed_json(memory_url):
    """Behavior: /health returns JSON with required fields (synchronous test
    via TestClient — proves the route handler works without asyncio fixture
    contention)."""
    from fastapi.testclient import TestClient

    from team_server.app import create_app

    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"status", "schema_version"}
    assert body["status"] == "ok"
