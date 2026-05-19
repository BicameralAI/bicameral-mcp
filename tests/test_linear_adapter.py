"""Tests for the Linear source adapter (#420 Phase 1a).

The HTTP boundary (urllib.request.urlopen) is mocked — sociable testing
doesn't apply to outbound calls to an external SaaS we don't run. Every
test below mocks at the *narrowest* seam: urlopen → bytes response. The
URL-parse logic, GraphQL document, normalization, secrets-store
integration, and error-handling all run end-to-end against the mocks.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from sources.linear.adapter import (
    LinearAdapter,
    normalize_issue_to_payload,
    parse_linear_url,
)
from sources.linear.client import LinearAPIError, query

# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://linear.app/myworkspace/issue/BIC-123", "BIC-123"),
        ("https://linear.app/myworkspace/issue/BIC-123/some-slug", "BIC-123"),
        ("https://linear.app/myworkspace/issue/BIC-123#comment-abc", "BIC-123"),
        ("https://linear.app/myworkspace/issue/eng-7/blah", "ENG-7"),
        ("  https://linear.app/myworkspace/issue/ABC-1  ", "ABC-1"),
    ],
)
def test_parse_linear_url_accepts_valid(url, expected):
    assert parse_linear_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar",
        "https://linear.app/myworkspace",
        "https://linear.app/myworkspace/issue/",
        "not-a-url",
        "",
        "https://linear.app/myworkspace/issue/123-456",  # no team prefix
    ],
)
def test_parse_linear_url_rejects_invalid(url):
    with pytest.raises(ValueError):
        parse_linear_url(url)


# ── Normalization ───────────────────────────────────────────────────────────


def test_normalize_issue_to_payload_full_shape():
    issue = {
        "identifier": "BIC-123",
        "title": "Investigate ingest gates",
        "description": "We need to refactor the ingest gate posture.",
        "completedAt": "2026-05-19T20:00:00Z",
        "updatedAt": "2026-05-18T10:00:00Z",
        "state": {"name": "Done"},
        "assignee": {"email": "dev@example.com", "name": "Dev"},
        "team": {"key": "BIC"},
        "comments": {
            "nodes": [
                {
                    "id": "c1",
                    "body": "Agreed — let's go with the WARN posture.",
                    "createdAt": "2026-05-19T10:00:00Z",
                    "user": {"email": "pm@example.com", "name": "PM"},
                },
                {
                    "id": "c2",
                    "body": "",  # empty — must be filtered
                    "user": {"email": "noisy@example.com"},
                },
            ]
        },
    }

    payload = normalize_issue_to_payload(issue, "BIC-123")

    assert payload["query"] == "Investigate ingest gates"
    assert payload["source"] == "linear"
    assert payload["title"] == "BIC-123"
    assert payload["date"] == "2026-05-19T20:00:00Z"
    assert payload["participants"] == ["dev@example.com", "pm@example.com"]
    assert len(payload["decisions"]) == 2  # description + 1 non-empty comment
    assert payload["decisions"][0]["description"] == "We need to refactor the ingest gate posture."
    assert payload["decisions"][0]["title"] == "BIC-123"
    assert payload["decisions"][1]["title"] == "BIC-123#comment-c1"


def test_normalize_skips_empty_description():
    issue = {
        "title": "T",
        "description": "   ",  # whitespace only
        "updatedAt": "2026-05-19T00:00:00Z",
        "comments": {"nodes": [{"id": "c1", "body": "real content", "user": {"email": "a@b.com"}}]},
    }
    payload = normalize_issue_to_payload(issue, "BIC-1")
    # Only the comment becomes a decision; the empty description is filtered.
    assert len(payload["decisions"]) == 1
    assert payload["decisions"][0]["description"] == "real content"


def test_normalize_handles_no_assignee_or_comments():
    issue = {
        "title": "Lonely",
        "description": "Some content",
        "updatedAt": "2026-05-19T00:00:00Z",
        "assignee": None,
        "comments": {"nodes": []},
    }
    payload = normalize_issue_to_payload(issue, "BIC-2")
    assert payload["participants"] == []
    assert len(payload["decisions"]) == 1


def test_normalize_falls_back_to_updated_at_when_completed_at_missing():
    issue = {
        "title": "WIP",
        "description": "x",
        "completedAt": None,
        "updatedAt": "2026-05-18T10:00:00Z",
        "comments": {"nodes": []},
    }
    payload = normalize_issue_to_payload(issue, "BIC-3")
    assert payload["date"] == "2026-05-18T10:00:00Z"


# ── GraphQL client error handling ───────────────────────────────────────────


def _mock_response(body: dict, status: int = 200):
    """Build a fake urlopen context manager returning ``body`` as JSON."""
    raw = json.dumps(body).encode("utf-8")
    resp = io.BytesIO(raw)
    resp.status = status  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    return _Ctx()


def test_query_returns_data_on_success():
    payload = {"data": {"issue": {"identifier": "BIC-1", "title": "t"}}}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        data = query(api_key="lin_abc", document="query{}", variables={"id": "BIC-1"})
    assert data == {"issue": {"identifier": "BIC-1", "title": "t"}}


def test_query_raises_on_http_error():
    err = urllib.error.HTTPError(
        url="https://api.linear.app/graphql",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(LinearAPIError) as exc_info:
            query(api_key="bad", document="query{}")
    assert exc_info.value.status_code == 401


def test_query_raises_on_graphql_errors():
    payload = {"errors": [{"message": "Issue not found"}], "data": None}
    with patch("urllib.request.urlopen", return_value=_mock_response(payload)):
        with pytest.raises(LinearAPIError, match="Issue not found"):
            query(api_key="lin_abc", document="query{}")


def test_query_raises_on_oversized_response():
    # Build a response that exceeds the 4 MiB cap.
    big = ("x" * (4 * 1024 * 1024 + 100)).encode("utf-8")
    resp = io.BytesIO(big)
    resp.status = 200  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Ctx()):
        with pytest.raises(LinearAPIError, match="exceeded"):
            query(api_key="lin_abc", document="query{}")


def test_query_raises_on_non_json_response():
    raw = b"not json at all"
    resp = io.BytesIO(raw)
    resp.status = 200  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Ctx()):
        with pytest.raises(LinearAPIError, match="non-JSON"):
            query(api_key="lin_abc", document="query{}")


# ── Adapter integration ─────────────────────────────────────────────────────


def test_adapter_can_handle_url():
    a = LinearAdapter()
    assert a.can_handle_url("https://linear.app/foo/issue/BIC-1")
    assert not a.can_handle_url("https://github.com/foo/bar")
    assert not a.can_handle_url("totally not a url")


def test_adapter_fetch_active_round_trip(monkeypatch):
    """Patch _resolve_api_key + urlopen; verify the full path returns
    a well-formed ingest payload."""
    payload_response = {
        "data": {
            "issue": {
                "identifier": "BIC-1",
                "title": "t",
                "description": "Some content",
                "completedAt": "2026-05-19T00:00:00Z",
                "updatedAt": "2026-05-19T00:00:00Z",
                "state": {"name": "Done"},
                "assignee": {"email": "a@b.com", "name": "A"},
                "team": {"key": "BIC"},
                "comments": {"nodes": []},
            }
        }
    }
    a = LinearAdapter()
    monkeypatch.setattr(a, "_resolve_api_key", lambda: "lin_test")

    with patch("urllib.request.urlopen", return_value=_mock_response(payload_response)):
        result = a.fetch_active("https://linear.app/foo/issue/BIC-1")

    assert result["source"] == "linear"
    assert result["title"] == "BIC-1"
    assert result["query"] == "t"
    assert len(result["decisions"]) == 1


def test_adapter_raises_when_api_key_missing(monkeypatch):
    """If secrets_store has no api_key, the adapter must surface the
    operator-facing setup hint, not a generic null-deref."""
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    # Ensure the dict fallback is empty for this test.
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()

    a = LinearAdapter()
    with pytest.raises(RuntimeError, match="API key not configured"):
        a.fetch_active("https://linear.app/foo/issue/BIC-1")


def test_adapter_raises_when_issue_not_found(monkeypatch):
    payload_response = {"data": {"issue": None}}
    a = LinearAdapter()
    monkeypatch.setattr(a, "_resolve_api_key", lambda: "lin_test")

    with patch("urllib.request.urlopen", return_value=_mock_response(payload_response)):
        with pytest.raises(RuntimeError, match="no issue"):
            a.fetch_active("https://linear.app/foo/issue/BIC-999")
