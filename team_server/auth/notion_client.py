"""Notion API client - internal-integration auth, no OAuth.

Pure async functions over httpx. Token resolution: NOTION_TOKEN env
preferred; falls back to YAML config's `notion.token`; raises
NotionAuthError if neither is set. Notion-Version header is pinned to
2022-06-28 (the stable version this code is tested against).
"""

from __future__ import annotations

import os
from typing import AsyncIterator, Optional

import httpx
import yaml

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionAuthError(RuntimeError):
    """Raised when no Notion integration token can be resolved."""


def load_token(config_path: Optional[str] = None) -> str:
    env = os.environ.get("NOTION_TOKEN")
    if env:
        return env
    if config_path and os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        token = (cfg.get("notion") or {}).get("token")
        if token:
            return token
    raise NotionAuthError("NOTION_TOKEN not set and notion.token absent in config")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


async def list_databases(token: str) -> list[tuple[str, str]]:
    """Return [(db_id, title), ...] for databases the integration sees."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{NOTION_API_BASE}/search",
            headers=_headers(token),
            json={"filter": {"property": "object", "value": "database"}},
        )
    resp.raise_for_status()
    out = []
    for entry in resp.json().get("results", []):
        title_parts = entry.get("title") or []
        title = "".join(p.get("plain_text", "") for p in title_parts) or "(untitled)"
        out.append((entry["id"], title))
    return out


async def query_database(
    token: str, db_id: str, watermark: Optional[str]
) -> AsyncIterator[dict]:
    """Yield page rows from a database, filtered by last_edited_time > watermark."""
    body: dict = {
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
    }
    if watermark:
        body["filter"] = {
            "timestamp": "last_edited_time",
            "last_edited_time": {"after": watermark},
        }
    cursor: Optional[str] = None
    async with httpx.AsyncClient() as client:
        while True:
            req_body = {**body, **({"start_cursor": cursor} if cursor else {})}
            resp = await client.post(
                f"{NOTION_API_BASE}/databases/{db_id}/query",
                headers=_headers(token),
                json=req_body,
            )
            resp.raise_for_status()
            payload = resp.json()
            for row in payload.get("results", []):
                yield row
            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")


async def fetch_page_blocks(token: str, page_id: str) -> list[dict]:
    """Return the flat list of top-level blocks for a page (paginated)."""
    out: list[dict] = []
    cursor: Optional[str] = None
    async with httpx.AsyncClient() as client:
        while True:
            params = {"start_cursor": cursor} if cursor else {}
            resp = await client.get(
                f"{NOTION_API_BASE}/blocks/{page_id}/children",
                headers=_headers(token),
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
            out.extend(payload.get("results", []))
            if not payload.get("has_more"):
                return out
            cursor = payload.get("next_cursor")
