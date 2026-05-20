"""Notion polling helper for Phase 2b passive ingest (#337).

Wraps the Phase 2 REST client with a database query that returns pages
edited strictly after the watermark. Notion has no webhook story for
shared workspaces, so polling is the only viable passive path.

Pagination cap: 20 pages × 100 results = 2000 pages per pull. Mirrors
the Phase 2 blocks pagination cap.
"""

from __future__ import annotations

_PAGE_SIZE = 100
_MAX_PAGES = 20


def list_recently_edited_pages(
    *,
    api_key: str,
    database_id: str,
    edited_after: str | None = None,
):
    """Return database pages with ``last_edited_time > edited_after``.

    Each result dict carries the full Notion page object (the polling
    adapter pulls ``id``, ``last_edited_time``, and the URL from it).

    Sorted ascending by ``last_edited_time`` via the Notion sort spec.

    ``edited_after`` is an ISO 8601 timestamp string. ``None`` returns
    every page in the database, oldest-edit first.

    Raises ``RuntimeError`` on Notion API failure with a message the
    polling adapter logs without advancing the watermark.
    """
    import json
    import urllib.error
    import urllib.request

    from sources.notion.client import (
        _API_BASE,
        _MAX_RESPONSE_BYTES,
        _NOTION_VERSION,
        _REQUEST_TIMEOUT_SECONDS,
        NotionAPIError,
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
        "User-Agent": "bicameral-mcp/source-notion-polling",
    }

    body: dict = {
        "page_size": _PAGE_SIZE,
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
    }
    if edited_after:
        body["filter"] = {
            "timestamp": "last_edited_time",
            "last_edited_time": {"after": edited_after},
        }

    results: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        if cursor is not None:
            body["start_cursor"] = cursor
        elif "start_cursor" in body:
            del body["start_cursor"]
        req = urllib.request.Request(
            f"{_API_BASE}/databases/{database_id}/query",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise NotionAPIError(
                        f"Notion response exceeded {_MAX_RESPONSE_BYTES} bytes",
                        status_code=resp.status,
                    )
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Notion database query failed for database_id={database_id!r}: "
                f"HTTP {exc.code} {exc.reason}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Notion database query network error: {exc.reason}") from exc
        except NotionAPIError as exc:
            raise RuntimeError(f"Notion database query failed: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Notion database query returned non-JSON: {exc}") from exc

        results.extend(data.get("results") or [])
        pages += 1
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        if not cursor:
            break
        if pages >= _MAX_PAGES:
            raise RuntimeError(
                f"Notion database {database_id!r} has more than "
                f"{_MAX_PAGES * _PAGE_SIZE} edited pages since the watermark — "
                "narrow the database or split the source config"
            )

    return results
