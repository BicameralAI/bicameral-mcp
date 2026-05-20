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


def test_slack_list_channels_returns_normalized_dicts():
    from sources.slack.client import list_channels

    body = {
        "ok": True,
        "channels": [
            {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "num_members": 42,
            },
            {
                "id": "C2",
                "name": "private-thing",
                "is_private": True,
                "is_member": False,
                "num_members": 5,
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        result = list_channels(token="xoxb-t")
    assert [c["id"] for c in result] == ["C1", "C2"]
    assert result[0]["is_private"] is False
    assert result[1]["is_private"] is True


def test_slack_list_channels_paginates():
    from sources.slack.client import list_channels

    page1 = {
        "ok": True,
        "channels": [
            {"id": "C1", "name": "a", "is_private": False, "is_member": True, "num_members": 1}
        ],
        "response_metadata": {"next_cursor": "cur"},
    }
    page2 = {
        "ok": True,
        "channels": [
            {"id": "C2", "name": "b", "is_private": False, "is_member": True, "num_members": 2}
        ],
        "response_metadata": {"next_cursor": ""},
    }
    with patch("urllib.request.urlopen", side_effect=[_mock_resp(page1), _mock_resp(page2)]):
        result = list_channels(token="xoxb-t")
    assert len(result) == 2


# ── Linear: list_teams ──────────────────────────────────────────────────────


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


def test_notion_list_databases():
    from sources.notion.client import list_databases

    body = {
        "results": [
            {
                "id": "db-1",
                "title": [{"plain_text": "Decision Log"}],
            },
            {
                "id": "db-2",
                "title": [{"plain_text": "Meeting "}, {"plain_text": "Notes"}],
            },
            # Untitled database — should fall back to "(untitled)".
            {"id": "db-3", "title": []},
        ],
        "has_more": False,
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        result = list_databases(api_key="secret_x")
    assert [d["title"] for d in result] == ["Decision Log", "Meeting Notes", "(untitled)"]


# ── GitHub: list_repos ──────────────────────────────────────────────────────


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


def test_cli_returns_1_when_slack_token_missing(capsys):
    from cli.source_list_cli import main

    exit_code = main(SimpleNamespace(source="slack", format="table"))
    assert exit_code == 1
    assert "not configured" in capsys.readouterr().err.lower()


def test_cli_returns_0_on_slack_success(capsys, monkeypatch):
    from cli.source_list_cli import main
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-x")
    body = {
        "ok": True,
        "channels": [
            {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "num_members": 7,
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        exit_code = main(SimpleNamespace(source="slack", format="table"))
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "C1" in out
    assert "general" in out


def test_cli_json_format_emits_array(capsys):
    from cli.source_list_cli import main
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-x")
    body = {
        "ok": True,
        "channels": [
            {
                "id": "C1",
                "name": "general",
                "is_private": False,
                "is_member": True,
                "num_members": 7,
            },
        ],
        "response_metadata": {"next_cursor": ""},
    }
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        main(SimpleNamespace(source="slack", format="json"))
    parsed = json.loads(capsys.readouterr().out)
    assert isinstance(parsed, list)
    assert parsed[0]["id"] == "C1"


def test_cli_returns_3_on_api_error(capsys):
    from cli.source_list_cli import main
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-x")
    with patch(
        "urllib.request.urlopen",
        return_value=_mock_resp({"ok": False, "error": "invalid_auth"}),
    ):
        exit_code = main(SimpleNamespace(source="slack", format="table"))
    assert exit_code == 3
    assert "API error" in capsys.readouterr().err


def test_cli_empty_result_renders_friendly_message(capsys):
    from cli.source_list_cli import main
    from secrets_store import put_secret

    put_secret(source_id="slack", key="api_key", value="xoxb-x")
    body = {"ok": True, "channels": [], "response_metadata": {"next_cursor": ""}}
    with patch("urllib.request.urlopen", return_value=_mock_resp(body)):
        exit_code = main(SimpleNamespace(source="slack", format="table"))
    assert exit_code == 0
    assert "no results" in capsys.readouterr().out.lower()
