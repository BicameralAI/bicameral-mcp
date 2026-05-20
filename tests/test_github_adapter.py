"""Tests for the GitHub source adapter (#337 Phase 3)."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from sources.github.adapter import (
    GitHubAdapter,
    _ParsedURL,
    normalize_commit_to_payload,
    normalize_issue_to_payload,
    normalize_pr_to_payload,
    parse_github_url,
)
from sources.github.client import GitHubAPIError, _parse_link_next

# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            "https://github.com/BicameralAI/bicameral-mcp/pull/337",
            _ParsedURL(kind="pull", owner="BicameralAI", repo="bicameral-mcp", identifier="337"),
        ),
        (
            "https://github.com/foo/bar/issues/42",
            _ParsedURL(kind="issue", owner="foo", repo="bar", identifier="42"),
        ),
        (
            "https://github.com/foo/bar/commit/abc1234",
            _ParsedURL(kind="commit", owner="foo", repo="bar", identifier="abc1234"),
        ),
        (
            "https://github.com/foo/bar/pull/337#issuecomment-123",
            _ParsedURL(kind="pull", owner="foo", repo="bar", identifier="337"),
        ),
    ],
)
def test_parse_github_url_accepts_valid(url, expected):
    assert parse_github_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://linear.app/foo/issue/BIC-1",
        "https://github.com/foo",
        "https://github.com/foo/bar",
        "https://github.com/foo/bar/wiki",
        "",
    ],
)
def test_parse_github_url_rejects_invalid(url):
    with pytest.raises(ValueError):
        parse_github_url(url)


# ── PR normalization ────────────────────────────────────────────────────────


def test_normalize_pr_full_shape():
    pull = {
        "number": 337,
        "title": "Unified integrations tracker",
        "body": "Pulling Linear/Notion/GitHub into one Active/Passive frame.",
        "merged_at": "2026-05-19T20:00:00Z",
        "base": {"repo": {"full_name": "BicameralAI/bicameral-mcp"}},
        "user": {"login": "WulfForge"},
    }
    reviews = [
        {
            "id": 1001,
            "state": "APPROVED",
            "body": "LGTM",
            "user": {"login": "reviewer1"},
        },
        {
            "id": 1002,
            "state": "COMMENTED",
            "body": "",  # empty — filtered
            "user": {"login": "noisy"},
        },
    ]
    comments = [
        {
            "id": 2001,
            "body": "Should we also handle Jira here?",
            "user": {"login": "pm-alice"},
        },
    ]

    payload = normalize_pr_to_payload(pull, reviews, comments)

    assert payload["source"] == "github"
    assert payload["title"] == "BicameralAI/bicameral-mcp#PR-337"
    assert payload["query"] == "Unified integrations tracker"
    assert payload["date"] == "2026-05-19T20:00:00Z"
    assert "WulfForge" in payload["participants"]
    assert "reviewer1" in payload["participants"]
    assert "pm-alice" in payload["participants"]
    # 1 PR body + 1 non-empty review + 1 comment = 3 decisions
    assert len(payload["decisions"]) == 3
    assert "[APPROVED] LGTM" in payload["decisions"][1]["description"]


def test_normalize_pr_empty_body_no_reviews():
    pull = {"number": 5, "title": "Quick fix", "body": "", "user": {"login": "dev"}}
    payload = normalize_pr_to_payload(pull, [], [])
    assert payload["decisions"] == []


# ── Issue normalization ─────────────────────────────────────────────────────


def test_normalize_issue_extracts_repo_from_html_url():
    issue = {
        "number": 42,
        "title": "Bug report",
        "body": "Found a bug.",
        "html_url": "https://github.com/foo/bar/issues/42",
        "user": {"login": "reporter"},
        "updated_at": "2026-05-19T00:00:00Z",
    }
    payload = normalize_issue_to_payload(issue, [])
    assert payload["title"] == "foo/bar#issue-42"
    assert payload["participants"] == ["reporter"]


# ── Commit normalization ────────────────────────────────────────────────────


def test_normalize_commit():
    commit = {
        "sha": "abc12345def",
        "commit": {
            "message": "decision: use the WARN posture\n\nMore detail here.",
            "author": {"email": "dev@example.com", "date": "2026-05-19T00:00:00Z"},
        },
    }
    payload = normalize_commit_to_payload(commit, "foo", "bar")
    assert payload["title"] == "foo/bar@abc12345"
    assert payload["query"] == "decision: use the WARN posture"
    assert payload["participants"] == ["dev@example.com"]
    assert len(payload["decisions"]) == 1


# ── Client error handling + Link parsing ────────────────────────────────────


def test_parse_link_next():
    link = (
        '<https://api.github.com/repos/x/y/issues/1/comments?page=2>; rel="next", '
        '<https://api.github.com/repos/x/y/issues/1/comments?page=5>; rel="last"'
    )
    assert _parse_link_next(link) == ("https://api.github.com/repos/x/y/issues/1/comments?page=2")


def test_parse_link_next_no_next():
    link = '<https://api.github.com/repos/x/y/issues/1/comments?page=1>; rel="last"'
    assert _parse_link_next(link) is None


# ── Adapter integration ─────────────────────────────────────────────────────


def _mock_response(body, status: int = 200, headers: dict | None = None):
    if isinstance(body, (dict, list)):
        raw = json.dumps(body).encode("utf-8")
    else:
        raw = body
    resp = io.BytesIO(raw)
    resp.status = status  # type: ignore[attr-defined]

    class _Headers:
        def __init__(self, h):
            self._h = h

        def items(self):
            return list(self._h.items())

        def get(self, key, default=None):
            return self._h.get(key, default)

    resp.headers = _Headers(headers or {})  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


def test_adapter_can_handle_url():
    a = GitHubAdapter()
    assert a.can_handle_url("https://github.com/foo/bar/pull/1")
    assert a.can_handle_url("https://github.com/foo/bar/issues/2")
    assert a.can_handle_url("https://github.com/foo/bar/commit/abcdef0")
    assert not a.can_handle_url("https://linear.app/foo/issue/BIC-1")


def test_adapter_fetch_active_pull(monkeypatch):
    pull_resp = {
        "number": 1,
        "title": "T",
        "body": "Body",
        "merged_at": "2026-01-01T00:00:00Z",
        "base": {"repo": {"full_name": "foo/bar"}},
        "user": {"login": "dev"},
    }
    reviews_resp = []
    comments_resp = []

    responses = [
        _mock_response(pull_resp),
        _mock_response(reviews_resp),
        _mock_response(comments_resp),
    ]
    a = GitHubAdapter()
    monkeypatch.setattr(a, "_resolve_api_key", lambda: "ghp_x")

    with patch("urllib.request.urlopen", side_effect=responses):
        result = a.fetch_active("https://github.com/foo/bar/pull/1")

    assert result["source"] == "github"
    assert result["title"] == "foo/bar#PR-1"


def test_adapter_fetch_active_commit(monkeypatch):
    commit_resp = {
        "sha": "abc1234",
        "commit": {
            "message": "decision: ship it",
            "author": {"email": "dev@x.com", "date": "2026-01-01T00:00:00Z"},
        },
    }
    a = GitHubAdapter()
    monkeypatch.setattr(a, "_resolve_api_key", lambda: "ghp_x")

    with patch("urllib.request.urlopen", return_value=_mock_response(commit_resp)):
        result = a.fetch_active("https://github.com/foo/bar/commit/abc1234")

    assert result["source"] == "github"
    assert "decision: ship it" in result["decisions"][0]["description"]


def test_adapter_raises_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()

    a = GitHubAdapter()
    with pytest.raises(RuntimeError, match="API key not configured"):
        a.fetch_active("https://github.com/foo/bar/pull/1")
