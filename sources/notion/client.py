"""Thin Notion REST client (#420 Phase 2 — active ingest).

Uses stdlib ``urllib.request`` for consistency with the Linear client.
Two endpoints used today:
    GET /v1/pages/{id}             — page metadata + properties
    GET /v1/blocks/{id}/children    — paginated block list

Notion requires a ``Notion-Version`` header — pinned to a stable version
to avoid implicit upgrades silently breaking the block walker.

Threat-model parity with the Linear client:
- Token in Authorization header (never URL).
- Response size capped at 4 MiB per call (Notion pages with megabytes of
  blocks are misuse — operator should narrow the page scope).
- Per-call 15s timeout.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_API_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"
_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_PAGINATION_PAGES = 20  # 20 * 100 blocks = 2000 — bounds large-page misuse


class NotionAPIError(RuntimeError):
    """Raised for any non-recoverable Notion API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def _get(*, api_key: str, path: str, params: dict | None = None) -> dict:
    """GET ``{_API_BASE}{path}`` with optional query params; return parsed JSON."""
    url = f"{_API_BASE}{path}"
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": _NOTION_VERSION,
        "User-Agent": "bicameral-mcp/source-notion",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise NotionAPIError(
                    f"Notion response exceeded {_MAX_RESPONSE_BYTES} bytes",
                    status_code=resp.status,
                )
    except urllib.error.HTTPError as exc:
        raise NotionAPIError(
            f"Notion API HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise NotionAPIError(f"Notion API network error: {exc.reason}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise NotionAPIError(f"Notion API returned non-JSON: {exc}") from exc


def get_page(*, api_key: str, page_id: str) -> dict:
    """Fetch a page's metadata + properties."""
    return _get(api_key=api_key, path=f"/pages/{page_id}")


def get_all_blocks(*, api_key: str, page_id: str) -> list[dict]:
    """Fetch every child block of ``page_id``, paginating until exhausted
    or until the per-call page cap fires (misuse bound)."""
    out: list[dict] = []
    cursor: str | None = None
    pages_fetched = 0
    while True:
        params: dict[str, str] = {"page_size": "100"}
        if cursor is not None:
            params["start_cursor"] = cursor
        resp = _get(api_key=api_key, path=f"/blocks/{page_id}/children", params=params)
        out.extend(resp.get("results", []))
        pages_fetched += 1
        if not resp.get("has_more"):
            break
        if pages_fetched >= _MAX_PAGINATION_PAGES:
            raise NotionAPIError(
                f"page has more than {_MAX_PAGINATION_PAGES * 100} blocks — "
                "narrow the ingest scope to a sub-page"
            )
        cursor = resp.get("next_cursor")
        if not cursor:
            break
    return out
