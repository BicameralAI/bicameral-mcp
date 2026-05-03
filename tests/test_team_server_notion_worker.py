"""Functionality tests for team_server Phase 2 - Notion ingest worker."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def memory_url(monkeypatch):
    monkeypatch.setenv("BICAMERAL_TEAM_SERVER_SURREAL_URL", "memory://")


def _row(page_id: str, title: str, last_edited: str = "2026-05-02T10:00:00Z") -> dict:
    return {
        "id": page_id,
        "last_edited_time": last_edited,
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": title}]},
        },
    }


@pytest.mark.asyncio
async def test_poll_once_iterates_databases_from_list_databases(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    queried = []

    async def fake_list_databases(token):
        return [("db1", "D1"), ("db2", "D2")]

    async def fake_query_database(token, db_id, watermark):
        queried.append(db_id)
        if False:
            yield {}
        return

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        assert queried == ["db1", "db2"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_writes_event_on_first_seen_row(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        yield _row("page1", "Decision: REST")

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": [text]}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT * FROM team_event WHERE author_email = 'team-server@notion.bicameral'"
        )
        assert len(rows) == 1
        assert rows[0]["event_type"] == "ingest"
        assert rows[0]["payload"]["source_type"] == "notion_database_row"
        assert rows[0]["payload"]["source_ref"] == "db1/page1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_is_idempotent_on_unchanged_row(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        yield _row("p1", "T1")

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": [text]}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT * FROM team_event WHERE author_email = 'team-server@notion.bicameral'"
        )
        assert len(rows) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_writes_new_event_on_edited_row(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    state = {"title": "T1"}

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        yield _row("p1", state["title"])

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": [text]}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        state["title"] = "T1-edited"
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT * FROM team_event WHERE author_email = 'team-server@notion.bicameral' "
            "ORDER BY created_at ASC"
        )
        assert len(rows) == 2
        assert "T1-edited" in str(rows[1]["payload"]["extraction"])
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_advances_watermark_to_max_last_edited_time_seen(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        yield _row("p1", "T1", last_edited="2026-05-02T10:00:00Z")
        yield _row("p2", "T2", last_edited="2026-05-02T11:00:00Z")

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT last_seen FROM source_watermark "
            "WHERE source_type = 'notion' AND resource_id = 'db1'"
        )
        assert len(rows) == 1
        assert rows[0]["last_seen"] == "2026-05-02T11:00:00Z"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_passes_stored_watermark_to_query_database_on_subsequent_pass(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    captured = {"watermarks": []}

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        captured["watermarks"].append(watermark)
        if False:
            yield {}

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        # Pre-seed the watermark
        await client.query(
            "CREATE source_watermark CONTENT { source_type: 'notion', "
            "resource_id: 'db1', last_seen: '2026-05-02T09:00:00Z' }"
        )
        await notion_worker.poll_once(client, "tok", stub_extractor)
        assert captured["watermarks"] == ["2026-05-02T09:00:00Z"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_does_not_advance_watermark_past_failure_point(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        yield _row("p1", "T1", last_edited="2026-05-02T10:00:00Z")
        raise httpx.HTTPError("simulated mid-iteration failure")

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT last_seen FROM source_watermark "
            "WHERE source_type = 'notion' AND resource_id = 'db1'"
        )
        # Watermark advances only to the row that successfully ingested
        assert len(rows) == 1
        assert rows[0]["last_seen"] == "2026-05-02T10:00:00Z"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_poll_once_skips_database_on_404_logs_and_continues(monkeypatch):
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    async def fake_list_databases(token):
        return [("db_bad", "D_BAD"), ("db_ok", "D_OK")]

    async def fake_query_database(token, db_id, watermark):
        if db_id == "db_bad":
            raise httpx.HTTPStatusError(
                "404",
                request=httpx.Request("POST", "https://x"),
                response=httpx.Response(404),
            )
        yield _row("p1", "T1")

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": []}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT * FROM team_event WHERE author_email = 'team-server@notion.bicameral'"
        )
        assert len(rows) == 1
        assert rows[0]["payload"]["source_ref"] == "db_ok/p1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_content_hash_uses_serialized_row_not_raw_page_dict(monkeypatch):
    """Re-running with a properties dict in different insertion order
    still produces changed=False on the second pass — content_hash is
    derived from the deterministically-serialized text, not the dict."""
    from team_server.db import build_client
    from team_server.schema import ensure_schema
    from team_server.workers import notion_worker

    state = {"order": "v1"}

    async def fake_list_databases(token):
        return [("db1", "D1")]

    async def fake_query_database(token, db_id, watermark):
        # Same content, different dict insertion order on the 2nd call
        if state["order"] == "v1":
            yield {
                "id": "p1",
                "last_edited_time": "2026-05-02T10:00:00Z",
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "T"}]},
                    "A": {"type": "select", "select": {"name": "1"}},
                    "B": {"type": "select", "select": {"name": "2"}},
                },
            }
        else:
            yield {
                "id": "p1",
                "last_edited_time": "2026-05-02T10:00:00Z",
                "properties": {
                    "B": {"type": "select", "select": {"name": "2"}},
                    "A": {"type": "select", "select": {"name": "1"}},
                    "Name": {"type": "title", "title": [{"plain_text": "T"}]},
                },
            }

    async def fake_fetch_page_blocks(token, page_id):
        return []

    monkeypatch.setattr(notion_worker.nc, "list_databases", fake_list_databases)
    monkeypatch.setattr(notion_worker.nc, "query_database", fake_query_database)
    monkeypatch.setattr(notion_worker.nc, "fetch_page_blocks", fake_fetch_page_blocks)

    async def stub_extractor(text):
        return {"decisions": [text]}

    client = build_client()
    await client.connect()
    try:
        await ensure_schema(client)
        await notion_worker.poll_once(client, "tok", stub_extractor)
        state["order"] = "v2"
        await notion_worker.poll_once(client, "tok", stub_extractor)
        rows = await client.query(
            "SELECT * FROM team_event WHERE author_email = 'team-server@notion.bicameral'"
        )
        assert len(rows) == 1
    finally:
        await client.close()
