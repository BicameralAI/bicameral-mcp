"""Functionality tests for team_server Phase 3 — canonical-extraction cache."""

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


@pytest.mark.asyncio
async def test_cache_hit_returns_existing_extraction():
    """Behavior: get_or_compute returns the persisted extraction without
    invoking compute_fn when the (source_type, source_ref, content_hash)
    tuple already exists in extraction_cache."""
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import get_or_compute
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        # Seed a cache row
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: 'slack', "
            "source_ref: 'C123/T456', content_hash: 'abc', "
            "canonical_extraction: { decisions: ['existing'] }, "
            "model_version: 'interim-claude-v1' }"
        )

        compute_calls = []

        async def compute_fn():
            compute_calls.append(1)
            return {"decisions": ["new"]}

        result = await get_or_compute(
            client,
            source_type="slack",
            source_ref="C123/T456",
            content_hash="abc",
            compute_fn=compute_fn,
            model_version="interim-claude-v1",
        )
        assert compute_calls == []  # NOT invoked
        assert result == {"decisions": ["existing"]}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_miss_invokes_compute_and_persists():
    """Behavior: cache miss invokes compute_fn, persists the result, AND a
    subsequent call with same key returns the cached value (no second
    compute_fn invocation)."""
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import get_or_compute
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        compute_calls = []

        async def compute_fn():
            compute_calls.append(1)
            return {"decisions": ["d1", "d2"]}

        first = await get_or_compute(
            client,
            source_type="slack",
            source_ref="C/T",
            content_hash="h1",
            compute_fn=compute_fn,
            model_version="interim-claude-v1",
        )
        assert compute_calls == [1]
        assert first == {"decisions": ["d1", "d2"]}

        second = await get_or_compute(
            client,
            source_type="slack",
            source_ref="C/T",
            content_hash="h1",
            compute_fn=compute_fn,
            model_version="interim-claude-v1",
        )
        assert compute_calls == [1]  # NOT invoked again
        assert second == first
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_keys_on_content_hash_changes():
    """Behavior: different content_hash with same (source_type, source_ref)
    produces a new cache row (Slack message edit -> re-extract)."""
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import get_or_compute
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        n = [0]

        async def compute_fn():
            n[0] += 1
            return {"decisions": [f"d{n[0]}"]}

        await get_or_compute(client, "slack", "C/T", "hash-A", compute_fn, "v1")
        await get_or_compute(client, "slack", "C/T", "hash-B", compute_fn, "v1")

        rows = await client.query(
            "SELECT * FROM extraction_cache WHERE source_ref = 'C/T'"
        )
        assert len(rows) == 2
        hashes = {r["content_hash"] for r in rows}
        assert hashes == {"hash-A", "hash-B"}
    finally:
        await client.close()
