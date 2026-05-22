"""Tests for #337 Phase 2b — Notion polling adapter."""

from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


def _mock_response(body: dict):
    raw = json.dumps(body).encode("utf-8")
    resp = io.BytesIO(raw)
    resp.status = 200

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


# ── poller.list_recently_edited_pages ───────────────────────────────────────


def test_list_recently_edited_returns_results():
    from sources.notion.poller import list_recently_edited_pages

    pages = [
        {"id": "p1", "last_edited_time": "2026-05-01T00:00:00Z", "url": "u1"},
        {"id": "p2", "last_edited_time": "2026-05-02T00:00:00Z", "url": "u2"},
    ]
    with patch(
        "urllib.request.urlopen",
        return_value=_mock_response({"results": pages, "has_more": False}),
    ):
        result = list_recently_edited_pages(api_key="k", database_id="db1")
    assert [p["id"] for p in result] == ["p1", "p2"]


def test_list_recently_edited_paginates():
    from sources.notion.poller import list_recently_edited_pages

    page1 = {
        "results": [{"id": "p1", "last_edited_time": "2026-05-01T00:00:00Z", "url": "u1"}],
        "has_more": True,
        "next_cursor": "c1",
    }
    page2 = {
        "results": [{"id": "p2", "last_edited_time": "2026-05-02T00:00:00Z", "url": "u2"}],
        "has_more": False,
    }
    with patch(
        "urllib.request.urlopen",
        side_effect=[_mock_response(page1), _mock_response(page2)],
    ):
        result = list_recently_edited_pages(api_key="k", database_id="db1")
    assert len(result) == 2


def test_list_recently_edited_includes_filter():
    from sources.notion.poller import list_recently_edited_pages

    captured: dict = {}

    def _capture_request(req, timeout):
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _mock_response({"results": [], "has_more": False})

    with patch("urllib.request.urlopen", side_effect=_capture_request):
        list_recently_edited_pages(
            api_key="k", database_id="db1", edited_after="2026-05-01T00:00:00Z"
        )

    assert captured["body"]["filter"]["last_edited_time"]["after"] == "2026-05-01T00:00:00Z"


def test_list_recently_edited_raises_on_http_error():
    import urllib.error

    from sources.notion.poller import list_recently_edited_pages

    err = urllib.error.HTTPError(
        url="https://api.notion.com/v1/databases/db1/query",
        code=403,
        msg="Forbidden",
        hdrs=None,
        fp=io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(RuntimeError, match="database query failed"):
            list_recently_edited_pages(api_key="k", database_id="db1")


# ── NotionPollingAdapter.pull ───────────────────────────────────────────────


def test_pull_returns_payloads_for_new_pages(tmp_path):
    from events.sources.notion import NotionPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="notion", key="api_key", value="secret_x")

    fake_pages = [
        {"id": "p1", "url": "https://www.notion.so/p1", "last_edited_time": "2026-05-01T00:00:00Z"},
        {"id": "p2", "url": "https://www.notion.so/p2", "last_edited_time": "2026-05-02T00:00:00Z"},
    ]
    fake_payload = {"source": "notion", "decisions": [], "title": "x"}

    with (
        patch(
            "sources.notion.poller.list_recently_edited_pages",
            return_value=fake_pages,
        ),
        patch(
            "sources.notion.adapter.NotionAdapter.fetch_active",
            return_value=fake_payload,
        ),
    ):
        adapter = NotionPollingAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"database_id": "db1"})

    assert len(result) == 2
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_pull_returns_empty_when_database_id_missing(tmp_path, capsys):
    from events.sources.notion import NotionPollingAdapter

    adapter = NotionPollingAdapter()
    result = adapter.pull(watermark_dir=tmp_path, config={})
    assert result == []
    assert "database_id is required" in capsys.readouterr().err


def test_pull_returns_empty_when_api_key_missing(tmp_path, capsys):
    from events.sources.notion import NotionPollingAdapter

    adapter = NotionPollingAdapter()
    result = adapter.pull(watermark_dir=tmp_path, config={"database_id": "db1"})
    assert result == []
    assert "api_key not configured" in capsys.readouterr().err


def test_pull_skips_individual_fetch_failures(tmp_path, capsys):
    from events.sources.notion import NotionPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="notion", key="api_key", value="secret_x")
    fake_pages = [
        {"id": "p1", "url": "https://www.notion.so/p1", "last_edited_time": "2026-05-01T00:00:00Z"},
        {"id": "p2", "url": "https://www.notion.so/p2", "last_edited_time": "2026-05-02T00:00:00Z"},
    ]

    def _flaky(self, url):
        if "p1" in url:
            raise RuntimeError("transient")
        return {"source": "notion", "decisions": [], "title": "p2"}

    with (
        patch(
            "sources.notion.poller.list_recently_edited_pages",
            return_value=fake_pages,
        ),
        patch(
            "sources.notion.adapter.NotionAdapter.fetch_active",
            new=_flaky,
        ),
    ):
        adapter = NotionPollingAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={"database_id": "db1"})

    assert len(result) == 1
    assert "p1" in capsys.readouterr().err
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_pull_passes_watermark_to_poller(tmp_path):
    from events.sources.notion import NotionPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="notion", key="api_key", value="secret_x")
    (tmp_path / "notion.json").write_text(json.dumps({"last_edited_time": "2026-05-01T00:00:00Z"}))

    captured: dict = {}

    def _capture(*, api_key, database_id, edited_after=None):
        captured["edited_after"] = edited_after
        return []

    with patch(
        "sources.notion.poller.list_recently_edited_pages",
        side_effect=_capture,
    ):
        adapter = NotionPollingAdapter()
        adapter.pull(watermark_dir=tmp_path, config={"database_id": "db1"})

    assert captured["edited_after"] == "2026-05-01T00:00:00Z"


def test_confirm_watermark_persists(tmp_path):
    from events.sources.notion import NotionPollingAdapter

    adapter = NotionPollingAdapter()
    adapter._watermark_path = tmp_path / "notion.json"
    adapter._pending_watermark = "2026-05-19T00:00:00Z"
    adapter.confirm_watermark()
    data = json.loads((tmp_path / "notion.json").read_text())
    assert data["last_edited_time"] == "2026-05-19T00:00:00Z"


def test_registered_in_ADAPTERS():
    from events.sources import ADAPTERS
    from events.sources.notion import NotionPollingAdapter

    assert ADAPTERS["notion"] is NotionPollingAdapter
