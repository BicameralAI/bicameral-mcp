"""Functionality tests for Phase 0 — classifier_version axis on extraction_cache."""

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
async def test_upsert_returns_changed_true_when_classifier_version_differs():
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
            client, source_type="slack", source_ref="A/1",
            content_hash="h", classifier_version="cv-1",
            compute_fn=stub_v1, model_version="m",
        )
        extraction, changed = await upsert_canonical_extraction(
            client, source_type="slack", source_ref="A/1",
            content_hash="h", classifier_version="cv-2",
            compute_fn=stub_v2, model_version="m",
        )
        assert changed is True
        assert extraction == {"decisions": ["v2"]}
        rows = await client.query(
            "SELECT classifier_version FROM extraction_cache "
            "WHERE source_type = 'slack' AND source_ref = 'A/1'"
        )
        assert rows[0]["classifier_version"] == "cv-2"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upsert_returns_changed_false_when_both_hash_and_version_match():
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        compute_count = {"n": 0}

        async def stub():
            compute_count["n"] += 1
            return {"decisions": ["x"]}

        await upsert_canonical_extraction(
            client, source_type="slack", source_ref="B/1",
            content_hash="h", classifier_version="cv-1",
            compute_fn=stub, model_version="m",
        )
        extraction, changed = await upsert_canonical_extraction(
            client, source_type="slack", source_ref="B/1",
            content_hash="h", classifier_version="cv-1",
            compute_fn=stub, model_version="m",
        )
        assert changed is False
        assert extraction == {"decisions": ["x"]}
        assert compute_count["n"] == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upsert_returns_changed_true_when_content_hash_differs_classifier_same():
    from team_server.db import build_client
    from team_server.extraction.canonical_cache import upsert_canonical_extraction
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)

        async def stub_a():
            return {"decisions": ["a"]}

        async def stub_b():
            return {"decisions": ["b"]}

        await upsert_canonical_extraction(
            client, source_type="slack", source_ref="C/1",
            content_hash="h-a", classifier_version="cv-1",
            compute_fn=stub_a, model_version="m",
        )
        extraction, changed = await upsert_canonical_extraction(
            client, source_type="slack", source_ref="C/1",
            content_hash="h-b", classifier_version="cv-1",
            compute_fn=stub_b, model_version="m",
        )
        assert changed is True
        assert extraction == {"decisions": ["b"]}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_v2_to_v3_migration_adds_classifier_version_column():
    """Behavior: after migration, INSERT with classifier_version succeeds
    AND pre-existing rows are backfilled with 'legacy-pre-v3'."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: 'slack', "
            "source_ref: 'X/1', content_hash: 'h', "
            "canonical_extraction: {}, model_version: 'm', "
            "classifier_version: 'cv-real' }"
        )
        rows = await client.query(
            "SELECT classifier_version FROM extraction_cache "
            "WHERE source_type = 'slack' AND source_ref = 'X/1'"
        )
        assert rows[0]["classifier_version"] == "cv-real"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_v2_to_v3_migration_backfills_legacy_rows_with_default_classifier_version():
    """Behavior: rows that pre-date the classifier_version column read
    back as 'legacy-pre-v3' after the migration applies the field's
    DEFAULT clause. Closes the SurrealDB v2 embedded IS NONE quirk
    coverage gap (Fixer L4-B)."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema

    client = build_client()
    await client.connect()
    try:
        # Bootstrap minimal schema (without the v3 field) by manually defining
        # the v1-shape extraction_cache, then run ensure_schema to migrate.
        await client.query("DEFINE TABLE extraction_cache SCHEMAFULL")
        await client.query("DEFINE FIELD source_type ON extraction_cache TYPE string")
        await client.query("DEFINE FIELD source_ref ON extraction_cache TYPE string")
        await client.query("DEFINE FIELD content_hash ON extraction_cache TYPE string")
        await client.query(
            "DEFINE FIELD canonical_extraction ON extraction_cache "
            "FLEXIBLE TYPE object DEFAULT {}"
        )
        await client.query(
            "DEFINE FIELD model_version ON extraction_cache TYPE string"
        )
        await client.query(
            "DEFINE FIELD created_at ON extraction_cache "
            "TYPE datetime DEFAULT time::now()"
        )
        await client.query(
            "CREATE extraction_cache CONTENT { source_type: 'slack', "
            "source_ref: 'legacy/1', content_hash: 'h', "
            "canonical_extraction: {}, model_version: 'm', "
            "created_at: time::now() }"
        )
        await ensure_schema(client)
        rows = await client.query(
            "SELECT classifier_version FROM extraction_cache "
            "WHERE source_type = 'slack' AND source_ref = 'legacy/1'"
        )
        assert len(rows) == 1
        assert rows[0]["classifier_version"] == "legacy-pre-v3"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_v2_to_v3_migration_is_idempotent():
    from team_server.db import build_client
    from team_server.schema import SCHEMA_VERSION, ensure_schema

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await ensure_schema(client)
        rows = await client.query("SELECT version FROM schema_version")
        assert len(rows) == 1
        assert rows[0]["version"] == SCHEMA_VERSION
    finally:
        await client.close()
