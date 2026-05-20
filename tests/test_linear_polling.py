"""Tests for #337 Phase 1b — Linear polling adapter.

Mocks at the narrowest seams: ``sources.linear.client.query`` for the
GraphQL boundary; ``sources.linear.adapter.LinearAdapter.fetch_active``
for the per-issue payload build. Watermark file I/O runs unmocked.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


# ── poller.list_completed_issues ────────────────────────────────────────────


def _conn(*nodes, has_next=False, cursor=None):
    return {
        "issues": {
            "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
            "nodes": list(nodes),
        }
    }


def test_list_completed_filters_to_non_null_completed_at():
    """The poller must drop nodes without completedAt even if the API
    leaks one (defense in depth — GraphQL filter should already exclude)."""
    from sources.linear.poller import list_completed_issues

    nodes = [
        {"identifier": "BIC-1", "url": "u1", "completedAt": "2026-05-01T00:00:00Z"},
        {"identifier": "BIC-2", "url": "u2", "completedAt": None},
        {"identifier": "BIC-3", "url": "u3", "completedAt": "2026-05-02T00:00:00Z"},
    ]
    with patch("sources.linear.client.query", return_value=_conn(*nodes)):
        result = list_completed_issues(api_key="k")
    assert [n["identifier"] for n in result] == ["BIC-1", "BIC-3"]


def test_list_completed_sorts_by_completed_at_ascending():
    from sources.linear.poller import list_completed_issues

    nodes = [
        {"identifier": "B", "url": "ub", "completedAt": "2026-05-02T00:00:00Z"},
        {"identifier": "A", "url": "ua", "completedAt": "2026-05-01T00:00:00Z"},
    ]
    with patch("sources.linear.client.query", return_value=_conn(*nodes)):
        result = list_completed_issues(api_key="k")
    assert [n["identifier"] for n in result] == ["A", "B"]


def test_list_completed_includes_filter_in_variables():
    from sources.linear.poller import list_completed_issues

    captured = {}

    def _capture(*, api_key, document, variables):
        captured.update(variables)
        return _conn()

    with patch("sources.linear.client.query", side_effect=_capture):
        list_completed_issues(
            api_key="k",
            completed_after="2026-05-01T00:00:00Z",
            team_keys=["BIC", "ENG"],
        )

    f = captured["filter"]
    assert f["completedAt"]["gt"] == "2026-05-01T00:00:00Z"
    assert f["team"] == {"key": {"in": ["BIC", "ENG"]}}


def test_list_completed_paginates():
    from sources.linear.poller import list_completed_issues

    page1 = _conn(
        {"identifier": "A", "url": "ua", "completedAt": "2026-05-01T00:00:00Z"},
        has_next=True,
        cursor="c1",
    )
    page2 = _conn(
        {"identifier": "B", "url": "ub", "completedAt": "2026-05-02T00:00:00Z"},
    )
    with patch("sources.linear.client.query", side_effect=[page1, page2]):
        result = list_completed_issues(api_key="k")
    assert len(result) == 2


def test_list_completed_raises_on_api_error():
    from sources.linear.client import LinearAPIError
    from sources.linear.poller import list_completed_issues

    with patch(
        "sources.linear.client.query",
        side_effect=LinearAPIError("bad", status_code=401),
    ):
        with pytest.raises(RuntimeError, match="issue listing failed"):
            list_completed_issues(api_key="k")


# ── LinearPollingAdapter.pull ───────────────────────────────────────────────


def test_pull_returns_payloads_for_new_issues(tmp_path):
    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="linear", key="api_key", value="lin_test")

    fake_issues = [
        {
            "identifier": "BIC-1",
            "url": "https://linear.app/x/issue/BIC-1",
            "completedAt": "2026-05-01T00:00:00Z",
        },
        {
            "identifier": "BIC-2",
            "url": "https://linear.app/x/issue/BIC-2",
            "completedAt": "2026-05-02T00:00:00Z",
        },
    ]
    fake_payload = {"source": "linear", "decisions": [], "title": "x"}

    with (
        patch(
            "sources.linear.poller.list_completed_issues",
            return_value=fake_issues,
        ),
        patch(
            "sources.linear.adapter.LinearAdapter.fetch_active",
            return_value=fake_payload,
        ),
    ):
        adapter = LinearPollingAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={})

    assert len(result) == 2
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_pull_returns_empty_when_api_key_missing(tmp_path, capsys):
    from events.sources.linear import LinearPollingAdapter

    # No put_secret call → no api_key stored.
    adapter = LinearPollingAdapter()
    result = adapter.pull(watermark_dir=tmp_path, config={})
    assert result == []
    err = capsys.readouterr().err
    assert "api_key not configured" in err


def test_pull_skips_individual_fetch_failures(tmp_path, capsys):
    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="linear", key="api_key", value="lin_test")

    fake_issues = [
        {
            "identifier": "BIC-1",
            "url": "https://linear.app/x/issue/BIC-1",
            "completedAt": "2026-05-01T00:00:00Z",
        },
        {
            "identifier": "BIC-2",
            "url": "https://linear.app/x/issue/BIC-2",
            "completedAt": "2026-05-02T00:00:00Z",
        },
    ]

    def _flaky(self, url):
        if "BIC-1" in url:
            raise RuntimeError("transient")
        return {"source": "linear", "decisions": [], "title": "BIC-2"}

    with (
        patch(
            "sources.linear.poller.list_completed_issues",
            return_value=fake_issues,
        ),
        patch(
            "sources.linear.adapter.LinearAdapter.fetch_active",
            new=_flaky,
        ),
    ):
        adapter = LinearPollingAdapter()
        result = adapter.pull(watermark_dir=tmp_path, config={})

    assert len(result) == 1
    assert "BIC-1" in capsys.readouterr().err
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


def test_pull_passes_watermark_to_poller(tmp_path):
    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="linear", key="api_key", value="lin_test")
    (tmp_path / "linear.json").write_text(json.dumps({"last_completed_at": "2026-05-01T00:00:00Z"}))

    captured = {}

    def _capture(*, api_key, completed_after, team_keys=None):
        captured["completed_after"] = completed_after
        captured["team_keys"] = team_keys
        return []

    with patch(
        "sources.linear.poller.list_completed_issues",
        side_effect=_capture,
    ):
        adapter = LinearPollingAdapter()
        adapter.pull(watermark_dir=tmp_path, config={"team_keys": ["BIC"]})

    assert captured["completed_after"] == "2026-05-01T00:00:00Z"
    assert captured["team_keys"] == ["BIC"]


def test_pull_starts_from_epoch_on_corrupt_watermark(tmp_path):
    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="linear", key="api_key", value="lin_test")
    (tmp_path / "linear.json").write_text("not-json{")

    captured = {}

    def _capture(*, api_key, completed_after, team_keys=None):
        captured["completed_after"] = completed_after
        return []

    with patch(
        "sources.linear.poller.list_completed_issues",
        side_effect=_capture,
    ):
        adapter = LinearPollingAdapter()
        adapter.pull(watermark_dir=tmp_path, config={})

    assert captured["completed_after"] is None


def test_confirm_watermark_persists(tmp_path):
    from events.sources.linear import LinearPollingAdapter

    adapter = LinearPollingAdapter()
    adapter._watermark_path = tmp_path / "linear.json"
    adapter._pending_watermark = "2026-05-19T00:00:00Z"
    adapter.confirm_watermark()
    data = json.loads((tmp_path / "linear.json").read_text())
    assert data["last_completed_at"] == "2026-05-19T00:00:00Z"


def test_confirm_watermark_noop_when_pending_none(tmp_path):
    from events.sources.linear import LinearPollingAdapter

    adapter = LinearPollingAdapter()
    adapter._watermark_path = tmp_path / "linear.json"
    adapter._pending_watermark = None
    adapter.confirm_watermark()
    assert not (tmp_path / "linear.json").exists()


def test_registered_in_ADAPTERS():
    from events.sources import ADAPTERS
    from events.sources.linear import LinearPollingAdapter

    assert ADAPTERS["linear"] is LinearPollingAdapter
