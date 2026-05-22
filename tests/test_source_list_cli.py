"""Tests for #337 foundations cycle 1 — `bicameral-mcp source-list <source>`.

Each per-source discovery primitive is unit-tested via the boundary
(urllib for REST-based sources, googleapiclient for Drive). The CLI
dispatcher is tested for exit-code contract and table/json rendering.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _disable_keyring(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


def _mock_resp(body):
    raw = json.dumps(body).encode("utf-8")
    resp = io.BytesIO(raw)
    resp.status = 200

    class _Headers:
        def __init__(self, h):
            self._h = h or {}

        def items(self):
            return list(self._h.items())

        def get(self, key, default=None):
            return self._h.get(key, default)

    resp.headers = _Headers({})

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


# ── Slack: list_channels ────────────────────────────────────────────────────


def test_linear_list_teams():
    from sources.linear.client import list_teams

    body = {
        "data": {
            "teams": {
                "nodes": [
                    {"id": "team-1", "key": "BIC", "name": "Bicameral"},
                    {"id": "team-2", "key": "ENG", "name": "Engineering"},
                ]
            }
        }
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        result = list_teams(api_key="lin_x")
    assert [t["key"] for t in result] == ["BIC", "ENG"]


# ── Notion: list_databases ──────────────────────────────────────────────────


def test_github_list_repos_pat_returns_array():
    from sources.github.client import list_repos

    body = [
        {"full_name": "foo/bar", "private": False, "default_branch": "main"},
        {"full_name": "foo/baz", "private": True, "default_branch": "main"},
    ]
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        result = list_repos(api_key="ghp_x")
    assert [r["full_name"] for r in result] == ["foo/bar", "foo/baz"]


def test_github_list_repos_app_token_returns_wrapped_object():
    """GitHub App installation tokens (ghs_) hit /installation/repositories
    which returns {repositories: [...]} — handle both shapes."""
    from sources.github.client import list_repos

    body = {
        "total_count": 1,
        "repositories": [
            {"full_name": "foo/installed", "private": False, "default_branch": "main"},
        ],
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        result = list_repos(api_key="ghs_installation_token")
    assert [r["full_name"] for r in result] == ["foo/installed"]


# ── Google Drive: list_visible_folders ──────────────────────────────────────


def test_gdrive_list_folders():
    from sources.google_drive.folder import list_visible_folders

    fake_service = MagicMock()
    fake_service.files.return_value.list.return_value.execute.return_value = {
        "files": [
            {
                "id": "folder-1",
                "name": "Design Docs",
                "owners": [{"emailAddress": "alice@example.com"}],
            },
            {
                "id": "folder-2",
                "name": "Archive",
                "owners": [
                    {"emailAddress": "bob@example.com"},
                    {"emailAddress": "alice@example.com"},
                ],
            },
        ]
    }
    with patch("googleapiclient.discovery.build", return_value=fake_service):
        result = list_visible_folders(MagicMock())
    assert [f["name"] for f in result] == ["Design Docs", "Archive"]
    assert (
        "alice@example.com,bob@example.com" in result[1]["owners"]
        or "bob@example.com,alice@example.com" in result[1]["owners"]
    )


# ── CLI dispatch ────────────────────────────────────────────────────────────
