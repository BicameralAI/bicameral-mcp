"""Tests for the Notion source adapter (#420 Phase 2).

Mirrors the Linear adapter test shape: mock urlopen at the boundary,
exercise URL parse / block walk / normalization / error paths unmocked.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from sources.notion.adapter import (
    NotionAdapter,
    normalize_page_to_payload,
    parse_notion_url,
)
from sources.notion.client import NotionAPIError, get_all_blocks, get_page

# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected_id",
    [
        (
            "https://www.notion.so/myws/Decision-Doc-1234567890abcdef1234567890abcdef",
            "12345678-90ab-cdef-1234-567890abcdef",
        ),
        (
            "https://www.notion.so/1234567890abcdef1234567890abcdef",
            "12345678-90ab-cdef-1234-567890abcdef",
        ),
        (
            "https://notion.so/myws/1234567890ABCDEF1234567890ABCDEF",
            "12345678-90ab-cdef-1234-567890abcdef",
        ),
        (
            "https://www.notion.so/myws/some-page-1234567890abcdef1234567890abcdef?v=tab",
            "12345678-90ab-cdef-1234-567890abcdef",
        ),
    ],
)
def test_parse_notion_url_accepts_valid(url, expected_id):
    assert parse_notion_url(url) == expected_id


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar",
        "https://www.notion.so/",
        "https://www.notion.so/myws/short-id-12345",
        "",
        "not-a-url",
    ],
)
def test_parse_notion_url_rejects_invalid(url):
    with pytest.raises(ValueError):
        parse_notion_url(url)


# ── Block text extraction + normalization ───────────────────────────────────


def _block(btype: str, text: str, **extra) -> dict:
    body = {"rich_text": [{"plain_text": text}]}
    body.update(extra)
    return {"type": btype, btype: body}


def test_normalize_full_page_with_mixed_blocks():
    page = {
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": "Decision Doc"}]},
        },
        "created_time": "2026-05-01T00:00:00Z",
        "last_edited_time": "2026-05-19T20:00:00Z",
        "created_by": {"name": "Alice", "id": "u1"},
        "last_edited_by": {"name": "Bob", "id": "u2"},
    }
    blocks = [
        _block("heading_1", "Context"),
        _block("paragraph", "We decided to use the WARN posture."),
        _block("heading_2", "Rationale"),
        _block("bulleted_list_item", "Pollers can't fail-fast"),
        _block("to_do", "Update docs", checked=False),
        _block("code", "x = 1", language="python"),
        {"type": "divider", "divider": {}},  # skipped
        {"type": "image", "image": {}},  # skipped
    ]

    payload = normalize_page_to_payload(page, blocks, "page-id")

    assert payload["source"] == "notion"
    assert payload["title"] == "Decision Doc"
    assert payload["query"] == "Decision Doc"
    assert payload["date"] == "2026-05-19T20:00:00Z"
    assert payload["participants"] == ["Alice", "Bob"]
    assert len(payload["decisions"]) == 1
    full_text = payload["decisions"][0]["description"]
    assert "# Context" in full_text
    assert "## Rationale" in full_text
    assert "- Pollers can't fail-fast" in full_text
    assert "- [ ] Update docs" in full_text
    assert "```python" in full_text
    # Divider and image are skipped, never appear.
    assert "image" not in full_text.lower() or "image" not in payload["decisions"][0]["description"]


def test_normalize_empty_page_drops_decision():
    page = {
        "properties": {"Name": {"type": "title", "title": []}},
        "created_time": "2026-05-19T00:00:00Z",
        "last_edited_time": "2026-05-19T00:00:00Z",
    }
    blocks = []
    payload = normalize_page_to_payload(page, blocks, "page-id")
    # No text → no decision row. Don't push empty decisions through.
    assert payload["decisions"] == []
    # But title falls back to the page_id when no title property is set.
    assert payload["title"] == "page-id"


def test_normalize_unchecked_vs_checked_todo():
    page = {"properties": {}, "created_time": "", "last_edited_time": ""}
    blocks = [
        _block("to_do", "Done thing", checked=True),
        _block("to_do", "Pending thing", checked=False),
    ]
    payload = normalize_page_to_payload(page, blocks, "p")
    desc = payload["decisions"][0]["description"]
    assert "- [x] Done thing" in desc
    assert "- [ ] Pending thing" in desc


def test_normalize_dedups_participants():
    page = {
        "properties": {"Name": {"type": "title", "title": [{"plain_text": "T"}]}},
        "created_by": {"name": "Same", "id": "u"},
        "last_edited_by": {"name": "Same", "id": "u"},
        "created_time": "",
        "last_edited_time": "",
    }
    payload = normalize_page_to_payload(page, [_block("paragraph", "x")], "p")
    assert payload["participants"] == ["Same"]


# ── REST client error handling ──────────────────────────────────────────────


def _mock_response(body: dict, status: int = 200):
    raw = json.dumps(body).encode("utf-8")
    resp = io.BytesIO(raw)
    resp.status = status  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


def test_get_page_success():
    expected = {"object": "page", "id": "p1"}
    with patch("urllib.request.urlopen", return_value=_mock_response(expected)):
        assert get_page(api_key="k", page_id="p1") == expected


def test_get_page_raises_on_http_404():
    err = urllib.error.HTTPError(
        url="https://api.notion.com/v1/pages/x",
        code=404,
        msg="Not Found",
        hdrs=None,
        fp=io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(NotionAPIError) as exc_info:
            get_page(api_key="k", page_id="x")
    assert exc_info.value.status_code == 404


def test_get_all_blocks_paginates_until_has_more_false():
    page1 = {
        "results": [_block("paragraph", "first")],
        "has_more": True,
        "next_cursor": "cursor-2",
    }
    page2 = {"results": [_block("paragraph", "second")], "has_more": False}

    responses = [_mock_response(page1), _mock_response(page2)]
    with patch("urllib.request.urlopen", side_effect=responses):
        blocks = get_all_blocks(api_key="k", page_id="p1")
    assert len(blocks) == 2


def test_get_all_blocks_caps_pagination_pages():
    """A page reporting has_more=True forever must hit the 20-page cap."""

    def _infinite_page(*args, **kwargs):
        # urlopen returns a context manager — each call needs its own.
        return _mock_response(
            {
                "results": [_block("paragraph", "x")],
                "has_more": True,
                "next_cursor": "c",
            }
        )

    with patch("urllib.request.urlopen", side_effect=_infinite_page):
        with pytest.raises(NotionAPIError, match="more than 2000"):
            get_all_blocks(api_key="k", page_id="p1")


# ── Adapter integration ─────────────────────────────────────────────────────


def test_adapter_can_handle_url():
    a = NotionAdapter()
    assert a.can_handle_url("https://www.notion.so/myws/page-1234567890abcdef1234567890abcdef")
    assert not a.can_handle_url("https://linear.app/foo/issue/BIC-1")


def test_adapter_fetch_active_round_trip(monkeypatch):
    page_resp = {
        "properties": {"Name": {"type": "title", "title": [{"plain_text": "T"}]}},
        "created_time": "2026-05-19T00:00:00Z",
        "last_edited_time": "2026-05-19T00:00:00Z",
    }
    blocks_resp = {
        "results": [_block("paragraph", "Decision content")],
        "has_more": False,
    }
    a = NotionAdapter()
    monkeypatch.setattr(a, "_resolve_api_key", lambda: "secret_x")

    responses = [_mock_response(page_resp), _mock_response(blocks_resp)]
    with patch("urllib.request.urlopen", side_effect=responses):
        result = a.fetch_active("https://www.notion.so/myws/T-1234567890abcdef1234567890abcdef")

    assert result["source"] == "notion"
    assert result["title"] == "T"
    assert "Decision content" in result["decisions"][0]["description"]


def test_adapter_raises_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()

    a = NotionAdapter()
    with pytest.raises(RuntimeError, match="API key not configured"):
        a.fetch_active("https://www.notion.so/myws/T-1234567890abcdef1234567890abcdef")
