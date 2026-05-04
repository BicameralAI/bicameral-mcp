"""Functionality tests for team_server canonical-extraction cache (v2 upsert contract)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")
    monkeypatch.setenv(
        "BICAMERAL_TEAM_SERVER_SECRET_KEY", "EYSr77qKo0UijHGnER5qYFBY5ZZePeWeE-ZMWYXyKKA="
    )


@pytest.mark.asyncio
async def test_cache_hit_returns_existing_extraction_without_invoking_compute():
    """v2 behavior: matching (source_type, source_ref, content_hash)
    triple returns (extraction, changed=False) without invoking compute_fn."""
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
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

        result, changed = await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C123/T456",
            content_hash="abc",
            classifier_version="legacy-pre-v3",
            compute_fn=compute_fn,
            model_version="interim-claude-v1",
        )
        assert compute_calls == []
        assert changed is False
        assert result == {"decisions": ["existing"]}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cache_miss_invokes_compute_persists_and_returns_changed_true():
    """v2 behavior: cache miss invokes compute_fn, persists, returns
    (extraction, changed=True). A subsequent call with the same key+hash
    returns changed=False without re-invoking compute_fn."""
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        compute_calls = []

        async def compute_fn():
            compute_calls.append(1)
            return {"decisions": ["d1", "d2"]}

        first, first_changed = await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C/T",
            content_hash="h1",
            classifier_version="legacy-pre-v3",
            compute_fn=compute_fn,
            model_version="interim-claude-v1",
        )
        assert compute_calls == [1]
        assert first_changed is True
        assert first == {"decisions": ["d1", "d2"]}

        second, second_changed = await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C/T",
            content_hash="h1",
            classifier_version="legacy-pre-v3",
            compute_fn=compute_fn,
            model_version="interim-claude-v1",
        )
        assert compute_calls == [1]
        assert second_changed is False
        assert second == first
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_content_hash_change_replaces_in_place_not_new_row():
    """v2 behavior: under the upsert contract, a different content_hash
    with same (source_type, source_ref) REPLACES the row in place — total
    row count remains 1 for that key. (v1 behavior produced a new row;
    that's been intentionally changed in the cache contract migration.)"""
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        n = [0]

        async def compute_fn():
            n[0] += 1
            return {"decisions": [f"d{n[0]}"]}

        await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C/T",
            content_hash="hash-A",
            classifier_version="legacy-pre-v3",
            compute_fn=compute_fn,
            model_version="v1",
        )
        await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C/T",
            content_hash="hash-B",
            classifier_version="legacy-pre-v3",
            compute_fn=compute_fn,
            model_version="v1",
        )

        rows = await client.query("SELECT * FROM extraction_cache WHERE source_ref = 'C/T'")
        assert len(rows) == 1
        assert rows[0]["content_hash"] == "hash-B"
        assert rows[0]["canonical_extraction"] == {"decisions": ["d2"]}
    finally:
        await client.close()
