"""Functionality tests for team_server Phase 0 — upsert-shaped canonical cache."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")


@pytest.mark.asyncio
async def test_upsert_returns_extraction_and_changed_true_on_first_write():
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)

        async def stub():
            return {"decisions": ["x"]}

        extraction, changed = await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C1/1.0",
            content_hash="h1",
            classifier_version="legacy-pre-v3",
            compute_fn=stub,
            model_version="interim-claude-v1",
        )
        assert extraction == {"decisions": ["x"]}
        assert changed is True
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upsert_returns_changed_false_on_same_hash():
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        call_count = {"n": 0}

        async def stub():
            call_count["n"] += 1
            return {"decisions": ["v1"]}

        await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C1/2.0",
            content_hash="h2",
            classifier_version="legacy-pre-v3",
            compute_fn=stub,
            model_version="interim-claude-v1",
        )
        extraction, changed = await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C1/2.0",
            content_hash="h2",
            classifier_version="legacy-pre-v3",
            compute_fn=stub,
            model_version="interim-claude-v1",
        )
        assert changed is False
        assert extraction == {"decisions": ["v1"]}
        assert call_count["n"] == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upsert_replaces_extraction_on_hash_change():
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)

        async def stub_v1():
            return {"decisions": ["v1"]}

        async def stub_v2():
            return {"decisions": ["v2"]}

        await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C1/3.0",
            content_hash="ha",
            classifier_version="legacy-pre-v3",
            compute_fn=stub_v1,
            model_version="interim-claude-v1",
        )
        extraction, changed = await upsert_canonical_extraction(
            client,
            source_type="slack",
            source_ref="C1/3.0",
            content_hash="hb",
            classifier_version="legacy-pre-v3",
            compute_fn=stub_v2,
            model_version="interim-claude-v1",
        )
        assert changed is True
        assert extraction == {"decisions": ["v2"]}
        rows = await client.query(
            "SELECT count() AS n FROM extraction_cache "
            "WHERE source_type = 'slack' AND source_ref = 'C1/3.0' GROUP ALL"
        )
        assert rows[0]["n"] == 1
        rows = await client.query(
            "SELECT canonical_extraction FROM extraction_cache "
            "WHERE source_type = 'slack' AND source_ref = 'C1/3.0'"
        )
        assert rows[0]["canonical_extraction"] == {"decisions": ["v2"]}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upsert_unique_index_is_source_type_and_ref_only():
    """Functionality: after migration, the unique index rejects a duplicate
    (source_type, source_ref) regardless of content_hash differences."""
    from ledger.client import LedgerError
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: 'slack', source_ref: 'C1/4.0', "
            "content_hash: 'h1', canonical_extraction: {}, model_version: 'm' }"
        )
        with pytest.raises(LedgerError):
            await client.query(
                "CREATE extraction_cache CONTENT { source_type: 'slack', source_ref: 'C1/4.0', "
                "content_hash: 'h2', canonical_extraction: {}, model_version: 'm' }"
            )
    finally:
        await client.close()
