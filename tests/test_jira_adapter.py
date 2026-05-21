"""Sociable tests for the Jira source adapter (#337 Phase A).

These are *sociable* tests: the real ``JiraAdapter``, the real
``normalize_issue_to_payload``, the real ``flatten_adf``, and the real
``client.get_issue`` HTTP-construction path all run end-to-end. The only
seam is the genuine external boundary — the outbound HTTP call to Jira,
which we cannot run against a real SaaS in CI. That seam is pinned to the
narrowest possible point: ``urllib.request.urlopen`` -> bytes response,
exactly the seam ``tests/test_linear_adapter.py`` uses.

URL parsing, the REST URL construction, the Basic-auth header, the ADF
flattening of description + comments, participant resolution, secrets-
store integration, and error handling all execute for real.
"""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from sources.jira.adapter import (
    JiraAdapter,
    normalize_issue_to_payload,
    parse_jira_url,
)
from sources.jira.client import JiraAPIError, get_issue

# ── URL parsing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected_base,expected_key",
    [
        (
            "https://acme.atlassian.net/browse/PROJ-123",
            "https://acme.atlassian.net",
            "PROJ-123",
        ),
        (
            "https://acme.atlassian.net/browse/PROJ-123/",
            "https://acme.atlassian.net",
            "PROJ-123",
        ),
        (
            "https://acme.atlassian.net/browse/PROJ-123?focusedId=99",
            "https://acme.atlassian.net",
            "PROJ-123",
        ),
        (
            "https://acme.atlassian.net/browse/PROJ-123#comment-99",
            "https://acme.atlassian.net",
            "PROJ-123",
        ),
        (
            "  https://my-team.atlassian.net/browse/ABC-1  ",
            "https://my-team.atlassian.net",
            "ABC-1",
        ),
        (
            "HTTPS://ACME.ATLASSIAN.NET/browse/proj-7",
            "https://acme.atlassian.net",
            "PROJ-7",
        ),
        (
            # The plan regex's ``(?:[/?#].*)?$`` tail accepts any trailing
            # path after the key (mirrors Linear's ``/<slug>`` acceptance).
            "https://acme.atlassian.net/browse/PROJ-123/extra/tail",
            "https://acme.atlassian.net",
            "PROJ-123",
        ),
    ],
)
def test_parse_jira_url_accepts_valid(url, expected_base, expected_key):
    base_url, issue_key = parse_jira_url(url)
    assert base_url == expected_base
    assert issue_key == expected_key


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/foo/bar",
        "https://acme.atlassian.net",
        "https://acme.atlassian.net/browse/",
        "https://acme.example.com/browse/PROJ-123",  # not atlassian.net
        "https://acme.atlassian.net/browse/PROJ",  # bare key, no number
        "https://acme.atlassian.net/browse/123-456",  # no project prefix
        "PROJ-123",  # bare key, not a URL
        "not-a-url",
        "",
    ],
)
def test_parse_jira_url_rejects_invalid(url):
    with pytest.raises(ValueError):
        parse_jira_url(url)


# ── can_handle_url ──────────────────────────────────────────────────────────


def test_adapter_can_handle_url():
    a = JiraAdapter()
    assert a.can_handle_url("https://acme.atlassian.net/browse/PROJ-1")
    assert a.can_handle_url("https://acme.atlassian.net/browse/PROJ-1#comment-2")
    assert not a.can_handle_url("https://github.com/foo/bar")
    assert not a.can_handle_url("https://linear.app/foo/issue/BIC-1")
    assert not a.can_handle_url("totally not a url")
    assert a.source_id == "jira"


# ── Fixtures: realistic Jira Get-issue responses ────────────────────────────


def _adf(*paragraph_texts: str) -> dict:
    """Build a minimal ADF doc, one paragraph per string."""
    return {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": t}]} for t in paragraph_texts
        ],
    }


def _full_issue() -> dict:
    """A realistic Get-issue response: ADF description + 2 ADF comments."""
    return {
        "id": "10001",
        "key": "PROJ-123",
        "fields": {
            "summary": "Decide the ingest gate posture",
            "description": _adf("We need to choose between hard-fail and WARN."),
            "status": {
                "name": "Done",
                "statusCategory": {"key": "done", "name": "Done"},
            },
            "created": "2026-05-19T09:00:00.000+0000",
            "updated": "2026-05-21T10:30:00.000+0000",
            "assignee": {
                "accountId": "acc-1",
                "displayName": "Dev One",
                "emailAddress": "dev@example.com",
            },
            "reporter": {
                "accountId": "acc-2",
                "displayName": "PM Two",
                "emailAddress": "pm@example.com",
            },
            "comment": {
                "startAt": 0,
                "maxResults": 100,
                "total": 2,
                "comments": [
                    {
                        "id": "9001",
                        "author": {
                            "accountId": "acc-3",
                            "displayName": "Reviewer Three",
                            "emailAddress": "rev@example.com",
                        },
                        "body": _adf("Agreed — go with WARN plus an audit emit."),
                        "created": "2026-05-20T11:00:00.000+0000",
                    },
                    {
                        "id": "9002",
                        "author": {
                            "accountId": "acc-1",
                            "displayName": "Dev One",
                            "emailAddress": "dev@example.com",
                        },
                        "body": _adf("Implemented in the ingest handler."),
                        "created": "2026-05-21T08:00:00.000+0000",
                    },
                ],
            },
        },
    }


# ── normalize_issue_to_payload (real, no seam) ──────────────────────────────


def test_normalize_full_shape():
    payload = normalize_issue_to_payload(_full_issue(), "PROJ-123")

    assert payload["query"] == "Decide the ingest gate posture"
    assert payload["source"] == "jira"
    assert payload["title"] == "PROJ-123"
    assert payload["date"] == "2026-05-21T10:30:00.000+0000"
    # assignee, reporter, then comment authors — de-duped (dev@ appears once).
    assert payload["participants"] == [
        "dev@example.com",
        "pm@example.com",
        "rev@example.com",
    ]
    # description + 2 comments, all flattened to plain text.
    assert len(payload["decisions"]) == 3
    assert payload["decisions"][0]["description"] == (
        "We need to choose between hard-fail and WARN."
    )
    assert payload["decisions"][0]["title"] == "PROJ-123"
    assert payload["decisions"][1]["title"] == "PROJ-123#comment-9001"
    assert payload["decisions"][2]["title"] == "PROJ-123#comment-9002"
    assert payload["decisions"][1]["description"] == ("Agreed — go with WARN plus an audit emit.")


def test_normalize_falls_back_to_created_when_updated_missing():
    issue = _full_issue()
    issue["fields"]["updated"] = None
    payload = normalize_issue_to_payload(issue, "PROJ-123")
    assert payload["date"] == "2026-05-19T09:00:00.000+0000"


def test_normalize_summary_falls_back_to_key():
    issue = _full_issue()
    issue["fields"]["summary"] = None
    payload = normalize_issue_to_payload(issue, "PROJ-123")
    assert payload["query"] == "PROJ-123"


def test_normalize_empty_description_is_omitted():
    """An issue with no description still ingests its comments."""
    issue = _full_issue()
    issue["fields"]["description"] = None
    payload = normalize_issue_to_payload(issue, "PROJ-123")
    # description dropped; both comments survive.
    assert len(payload["decisions"]) == 2
    assert payload["decisions"][0]["title"] == "PROJ-123#comment-9001"


def test_normalize_whitespace_only_comment_filtered():
    issue = _full_issue()
    # Replace one comment body with a whitespace-only ADF doc.
    issue["fields"]["comment"]["comments"][1]["body"] = _adf("   ")
    payload = normalize_issue_to_payload(issue, "PROJ-123")
    # description + 1 non-empty comment; the whitespace comment is dropped.
    assert len(payload["decisions"]) == 2
    titles = [d["title"] for d in payload["decisions"]]
    assert "PROJ-123#comment-9002" not in titles
    # The whitespace commenter is not a participant either.
    assert "dev@example.com" in payload["participants"]  # still assignee


def test_normalize_participant_fallback_to_display_name():
    """A comment author with no emailAddress falls back to displayName."""
    issue = _full_issue()
    issue["fields"]["comment"]["comments"][0]["author"] = {
        "accountId": "acc-9",
        "displayName": "Privacy Conscious",
        # emailAddress omitted by privacy settings
    }
    payload = normalize_issue_to_payload(issue, "PROJ-123")
    assert "Privacy Conscious" in payload["participants"]


def test_normalize_empty_issue_yields_empty_decisions():
    issue = {
        "id": "1",
        "key": "PROJ-1",
        "fields": {
            "summary": "Empty one",
            "description": None,
            "updated": "2026-05-21T00:00:00.000+0000",
            "comment": {"comments": []},
        },
    }
    payload = normalize_issue_to_payload(issue, "PROJ-1")
    assert payload["decisions"] == []
    assert payload["participants"] == []


# ── HTTP boundary seam (mirrors test_linear_adapter._mock_response) ─────────


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


# ── client.get_issue: real construction, seamed network ─────────────────────


def test_get_issue_returns_parsed_body_and_builds_correct_request():
    captured: dict = {}

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        captured["method"] = req.get_method()
        return _mock_response(_full_issue())

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        issue = get_issue(
            base_url="https://acme.atlassian.net",
            issue_key="PROJ-123",
            email="dev@example.com",
            token="secret-token-xyz",
        )

    assert issue["key"] == "PROJ-123"
    assert captured["method"] == "GET"
    assert captured["url"].startswith(
        "https://acme.atlassian.net/rest/api/3/issue/PROJ-123?fields="
    )
    # Auth header is Basic + base64; never the raw token.
    auth = captured["headers"]["authorization"]
    assert auth.startswith("Basic ")
    assert "secret-token-xyz" not in auth
    # The fields list requests the decision-bearing fields.
    assert "description" in captured["url"]
    assert "comment" in captured["url"]


def test_get_issue_raises_on_http_error_without_leaking_token():
    err = urllib.error.HTTPError(
        url="https://acme.atlassian.net/rest/api/3/issue/PROJ-1",
        code=401,
        msg="Unauthorized",
        hdrs=None,
        fp=io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(JiraAPIError) as exc_info:
            get_issue(
                base_url="https://acme.atlassian.net",
                issue_key="PROJ-1",
                email="dev@example.com",
                token="super-secret-token",
            )
    assert exc_info.value.status_code == 401
    # The token must never appear in the exception message.
    assert "super-secret-token" not in str(exc_info.value)


def test_get_issue_raises_on_oversized_response():
    big = ("x" * (8 * 1024 * 1024 + 100)).encode("utf-8")
    resp = io.BytesIO(big)
    resp.status = 200  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Ctx()):
        with pytest.raises(JiraAPIError, match="exceeded"):
            get_issue(
                base_url="https://acme.atlassian.net",
                issue_key="PROJ-1",
                email="dev@example.com",
                token="t",
            )


def test_get_issue_raises_on_non_json_response():
    raw = b"not json at all"
    resp = io.BytesIO(raw)
    resp.status = 200  # type: ignore[attr-defined]

    class _Ctx:
        def __enter__(self):
            return resp

        def __exit__(self, *args):
            return False

    with patch("urllib.request.urlopen", return_value=_Ctx()):
        with pytest.raises(JiraAPIError, match="non-JSON"):
            get_issue(
                base_url="https://acme.atlassian.net",
                issue_key="PROJ-1",
                email="dev@example.com",
                token="t",
            )


# ── JiraAdapter.fetch_active: full round trip, seamed network ───────────────


def test_fetch_active_happy_path(monkeypatch):
    """Real adapter + real normalizer + real flattener; only the HTTP
    boundary is seamed."""
    a = JiraAdapter()
    monkeypatch.setattr(a, "_resolve_auth", lambda: ("dev@example.com", "tok"))

    with patch("urllib.request.urlopen", return_value=_mock_response(_full_issue())):
        result = a.fetch_active("https://acme.atlassian.net/browse/PROJ-123")

    assert result["source"] == "jira"
    assert result["title"] == "PROJ-123"
    assert result["query"] == "Decide the ingest gate posture"
    assert len(result["decisions"]) == 3
    # Decisions are flattened plain text, not ADF dicts.
    assert all(isinstance(d["description"], str) for d in result["decisions"])
    assert result["decisions"][1]["title"] == "PROJ-123#comment-9001"
    assert result["decisions"][2]["title"] == "PROJ-123#comment-9002"


def test_fetch_active_comment_only_issue(monkeypatch):
    """An issue with no description still ingests its comments."""
    issue = _full_issue()
    issue["fields"]["description"] = None
    a = JiraAdapter()
    monkeypatch.setattr(a, "_resolve_auth", lambda: ("dev@example.com", "tok"))

    with patch("urllib.request.urlopen", return_value=_mock_response(issue)):
        result = a.fetch_active("https://acme.atlassian.net/browse/PROJ-123")

    assert len(result["decisions"]) == 2
    assert result["decisions"][0]["title"] == "PROJ-123#comment-9001"


def test_fetch_active_participant_fallback(monkeypatch):
    """A comment author with no emailAddress contributes its displayName."""
    issue = _full_issue()
    issue["fields"]["comment"]["comments"][0]["author"] = {
        "accountId": "acc-x",
        "displayName": "No Email User",
    }
    a = JiraAdapter()
    monkeypatch.setattr(a, "_resolve_auth", lambda: ("dev@example.com", "tok"))

    with patch("urllib.request.urlopen", return_value=_mock_response(issue)):
        result = a.fetch_active("https://acme.atlassian.net/browse/PROJ-123")

    assert "No Email User" in result["participants"]


def test_fetch_active_raises_when_issue_has_no_fields(monkeypatch):
    a = JiraAdapter()
    monkeypatch.setattr(a, "_resolve_auth", lambda: ("dev@example.com", "tok"))

    with patch("urllib.request.urlopen", return_value=_mock_response({"key": "PROJ-9"})):
        with pytest.raises(RuntimeError, match="no issue"):
            a.fetch_active("https://acme.atlassian.net/browse/PROJ-9")


# ── _resolve_auth: real secrets_store integration ───────────────────────────


def test_resolve_auth_raises_when_secret_missing(monkeypatch):
    """With no secrets configured, the adapter surfaces operator-facing
    setup guidance — and never a secret value."""
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()

    a = JiraAdapter()
    with pytest.raises(RuntimeError) as exc_info:
        a.fetch_active("https://acme.atlassian.net/browse/PROJ-1")
    msg = str(exc_info.value)
    assert "credentials not configured" in msg
    assert "api_email" in msg
    assert "api_token" in msg


def test_resolve_auth_returns_both_secrets(monkeypatch):
    """When both secrets are present, _resolve_auth returns the pair."""
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    from secrets_store import put_secret

    put_secret(source_id="jira", key="api_email", value="real@example.com")
    put_secret(source_id="jira", key="api_token", value="real-token")

    a = JiraAdapter()
    email, token = a._resolve_auth()
    assert email == "real@example.com"
    assert token == "real-token"


def test_resolve_auth_raises_when_only_email_present(monkeypatch):
    """A partial config (email but no token) is still a hard failure."""
    monkeypatch.setenv("BICAMERAL_KEYRING_DISABLE", "1")
    from secrets_store.store import _reset_for_tests

    _reset_for_tests()
    from secrets_store import put_secret

    put_secret(source_id="jira", key="api_email", value="real@example.com")

    a = JiraAdapter()
    with pytest.raises(RuntimeError, match="credentials not configured"):
        a._resolve_auth()
