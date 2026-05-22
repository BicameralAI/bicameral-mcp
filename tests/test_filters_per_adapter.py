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
