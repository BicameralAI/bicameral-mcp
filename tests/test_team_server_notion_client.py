"""Functionality tests for team_server Phase 1 - Notion API client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_load_token_prefers_env_over_config(monkeypatch, tmp_path):
    from team_server.auth import notion_client as nc

    monkeypatch.setenv("NOTION_TOKEN", "env_value")
    cfg = tmp_path / "c.yml"
    cfg.write_text("notion:\n  token: config_value\n")
    assert nc.load_token(str(cfg)) == "env_value"


def test_load_token_falls_back_to_config_when_env_unset(monkeypatch, tmp_path):
    from team_server.auth import notion_client as nc

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = tmp_path / "c.yml"
    cfg.write_text("notion:\n  token: config_value\n")
    assert nc.load_token(str(cfg)) == "config_value"


def test_load_token_raises_when_neither_set(monkeypatch, tmp_path):
    from team_server.auth import notion_client as nc

    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    cfg = tmp_path / "c.yml"
    cfg.write_text("notion: {}\n")
    with pytest.raises(nc.NotionAuthError):
        nc.load_token(str(cfg))


def _mk_transport(handler):
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_list_databases_returns_only_databases_filter(monkeypatch):
    from team_server.auth import notion_client as nc

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={
            "results": [
                {"object": "database", "id": "db1", "title": [{"plain_text": "D1"}]},
                {"object": "database", "id": "db2", "title": [{"plain_text": "D2"}]},
            ]
        })

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        nc.httpx, "AsyncClient",
        lambda *a, **kw: real_async_client(transport=_mk_transport(handler)),
    )
    out = await nc.list_databases("tok")
    assert out == [("db1", "D1"), ("db2", "D2")]
    assert captured["body"] == {"filter": {"property": "object", "value": "database"}}


@pytest.mark.asyncio
async def test_query_database_passes_last_edited_time_filter_when_watermark_given(monkeypatch):
    from team_server.auth import notion_client as nc

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"results": [], "has_more": False})

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        nc.httpx, "AsyncClient",
        lambda *a, **kw: real_async_client(transport=_mk_transport(handler)),
    )
    async for _ in nc.query_database("tok", "db1", "2026-05-02T00:00:00Z"):
        pass
    assert captured["body"]["filter"] == {
        "timestamp": "last_edited_time",
        "last_edited_time": {"after": "2026-05-02T00:00:00Z"},
    }

    captured.clear()
    async for _ in nc.query_database("tok", "db1", None):
        pass
    assert "filter" not in captured["body"]


@pytest.mark.asyncio
async def test_fetch_page_blocks_paginates_until_has_more_false(monkeypatch):
    from team_server.auth import notion_client as nc

    state = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["page"] += 1
        if state["page"] == 1:
            return httpx.Response(200, json={
                "results": [{"id": "b1"}], "has_more": True, "next_cursor": "c1",
            })
        if state["page"] == 2:
            return httpx.Response(200, json={
                "results": [{"id": "b2"}], "has_more": True, "next_cursor": "c2",
            })
        return httpx.Response(200, json={
            "results": [{"id": "b3"}], "has_more": False,
        })

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        nc.httpx, "AsyncClient",
        lambda *a, **kw: real_async_client(transport=_mk_transport(handler)),
    )
    out = await nc.fetch_page_blocks("tok", "page1")
    assert [b["id"] for b in out] == ["b1", "b2", "b3"]


@pytest.mark.asyncio
async def test_notion_version_header_is_pinned(monkeypatch):
    from team_server.auth import notion_client as nc

    captured = {"versions": []}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["versions"].append(request.headers.get("Notion-Version"))
        return httpx.Response(200, json={"results": [], "has_more": False})

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        nc.httpx, "AsyncClient",
        lambda *a, **kw: real_async_client(transport=_mk_transport(handler)),
    )
    await nc.list_databases("tok")
    await nc.fetch_page_blocks("tok", "p1")
    async for _ in nc.query_database("tok", "db1", None):
        pass
    assert all(v == nc.NOTION_VERSION for v in captured["versions"])
    assert len(captured["versions"]) >= 3
