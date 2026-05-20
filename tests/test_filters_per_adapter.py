"""End-to-end tests for filter wiring across all polling adapters.

Verifies the universal-filter integration in each polling adapter:
- Slack: per-resource + source-level merge
- Linear: source-level only
- Notion: source-level only
- GitHub: per-resource (repo) + source-level merge
- Google Drive: source-level only

For each: a passing filter lets items through, a rejecting filter
drops them, malformed config falls through to no-filter (operator
gets stderr warning, poller continues).
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


# ── Slack: keyword_include + per-resource override ──────────────────────────


def test_slack_source_level_keyword_filter_drops_unmatched(tmp_path):
    from events.sources.slack import SlackPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-t")

    fake_msgs = [
        {"ts": "1700000001.000000", "user": "U1", "text": "decided to ship"},
        {"ts": "1700000002.000000", "user": "U2", "text": "lunch?"},
    ]
    with (
        patch("sources.slack.poller.list_new_messages", return_value=fake_msgs),
        patch("sources.slack.client.get_user_info", return_value={}),
    ):
        adapter = SlackPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "channels": ["C01A"],
                "filters": {"keyword_include": ["decided"]},
            },
        )

    assert len(result) == 1
    # Watermark advances past the filtered-out message anyway.
    assert adapter._pending_watermarks["C01A"] == "1700000002.000000"


def test_slack_per_channel_override_takes_precedence(tmp_path):
    from events.sources.slack import SlackPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-t")

    # Same messages would be filtered by source-level filter, but
    # the per-channel override clears it for C01B by widening
    # the keyword_include list.
    msgs_a = [{"ts": "1700000001.000000", "user": "U1", "text": "lunch?"}]
    msgs_b = [{"ts": "1700000002.000000", "user": "U2", "text": "lunch?"}]

    call_count = {"n": 0}

    def _list(*, token, channel, oldest=None):
        call_count["n"] += 1
        return {"C01A": msgs_a, "C01B": msgs_b}[channel]

    with (
        patch("sources.slack.poller.list_new_messages", side_effect=_list),
        patch("sources.slack.client.get_user_info", return_value={}),
    ):
        adapter = SlackPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "channels": [
                    "C01A",  # inherits source-level filter — "lunch?" rejected
                    {"id": "C01B", "filters": {"keyword_include": ["lunch"]}},
                ],
                "filters": {"keyword_include": ["decided"]},
            },
        )

    # Only C01B's message survives.
    assert len(result) == 1


def test_slack_malformed_filter_falls_through(tmp_path, capsys):
    from events.sources.slack import SlackPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-t")
    fake_msgs = [{"ts": "1700000001.000000", "user": "U1", "text": "decided"}]
    with (
        patch("sources.slack.poller.list_new_messages", return_value=fake_msgs),
        patch("sources.slack.client.get_user_info", return_value={}),
    ):
        adapter = SlackPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            # Typo'd field — pydantic rejects → no-filter fallback.
            config={"channels": ["C01A"], "filters": {"keywords_include": ["x"]}},
        )

    err = capsys.readouterr().err
    assert "malformed filter block" in err
    # Items still flow through (no-filter fallback).
    assert len(result) == 1


# ── Linear: source-level keyword filter ─────────────────────────────────────


def test_linear_keyword_include_filters_issues(tmp_path):
    from events.sources.linear import LinearPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="linear", key="api_key", value="lin_t")

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

    def _fetch(self, url):
        if "BIC-1" in url:
            return {"query": "Decided to refactor", "decisions": [], "participants": []}
        return {"query": "Random chatter", "decisions": [], "participants": []}

    with (
        patch("sources.linear.poller.list_completed_issues", return_value=fake_issues),
        patch("sources.linear.adapter.LinearAdapter.fetch_active", new=_fetch),
    ):
        adapter = LinearPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={"filters": {"keyword_include": ["decided"]}},
        )

    assert len(result) == 1
    # Watermark advances past the filtered issue.
    assert adapter._pending_watermark == "2026-05-02T00:00:00Z"


# ── Notion: source-level time_window_after ──────────────────────────────────


def test_notion_time_window_filter(tmp_path):
    from events.sources.notion import NotionPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="notion", key="api_key", value="secret_t")

    fake_pages = [
        {"id": "p1", "url": "https://notion.so/p1", "last_edited_time": "2026-04-01T00:00:00Z"},
        {"id": "p2", "url": "https://notion.so/p2", "last_edited_time": "2026-06-01T00:00:00Z"},
    ]
    fake_payload = {"query": "x", "decisions": [], "participants": []}

    with (
        patch("sources.notion.poller.list_recently_edited_pages", return_value=fake_pages),
        patch("sources.notion.adapter.NotionAdapter.fetch_active", return_value=fake_payload),
    ):
        adapter = NotionPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "database_id": "db1",
                "filters": {"time_window_after": "2026-05-01T00:00:00Z"},
            },
        )

    assert len(result) == 1


# ── GitHub: per-repo override ───────────────────────────────────────────────


def test_github_per_repo_filter_override(tmp_path):
    from events.sources.github import GitHubPollingAdapter
    from secrets_store import put_secret

    put_secret(source_id="github", key="api_key", value="ghp_t")

    fake_a = [
        {
            "number": 1,
            "html_url": "https://github.com/foo/bar/pull/1",
            "updated_at": "2026-05-01T00:00:00Z",
            "merged_at": "2026-05-01T01:00:00Z",
        },
    ]
    fake_b = [
        {
            "number": 2,
            "html_url": "https://github.com/foo/baz/pull/2",
            "updated_at": "2026-05-02T00:00:00Z",
            "merged_at": "2026-05-02T01:00:00Z",
        },
    ]

    def _list(*, api_key, owner, repo, updated_after=None):
        return {"bar": fake_a, "baz": fake_b}[repo]

    def _fetch(self, url):
        return {"query": "merged PR", "decisions": [], "participants": ["bot"]}

    with (
        patch("sources.github.poller.list_merged_pulls_since", side_effect=_list),
        patch("sources.github.adapter.GitHubAdapter.fetch_active", new=_fetch),
    ):
        adapter = GitHubPollingAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "repos": [
                    "foo/bar",  # inherits source-level author_exclude — rejects "bot"
                    {"owner_repo": "foo/baz", "filters": {"author_exclude": []}},  # clears exclude
                ],
                "filters": {"author_exclude": ["bot"]},
            },
        )

    # foo/bar PR filtered out (bot author rejected); foo/baz PR passes.
    assert len(result) == 1


# ── Google Drive: source-level filter ───────────────────────────────────────


def test_google_drive_keyword_exclude(tmp_path):
    from unittest.mock import MagicMock

    from events.sources.google_drive import GoogleDriveFolderAdapter

    fake_docs = [
        {"id": "d1", "name": "Draft v1", "modifiedTime": "2026-05-01T00:00:00Z"},
        {"id": "d2", "name": "Final spec", "modifiedTime": "2026-05-02T00:00:00Z"},
    ]

    def _fetch(self, url):
        if "d1" in url:
            return {"query": "Draft content TBD", "decisions": [], "participants": []}
        return {"query": "Final decision", "decisions": [], "participants": []}

    with (
        patch("sources.google_drive.auth.load_credentials", return_value=MagicMock()),
        patch("sources.google_drive.folder.list_docs_in_folder", return_value=fake_docs),
        patch("sources.google_drive.adapter.GoogleDriveAdapter.fetch_active", new=_fetch),
    ):
        adapter = GoogleDriveFolderAdapter()
        result = adapter.pull(
            watermark_dir=tmp_path,
            config={
                "folder_id": "folder1",
                "filters": {"keyword_exclude": ["draft"]},
            },
        )

    assert len(result) == 1
