"""Solitary tests for the Jira JQL poller (#337 Jira Phase E).

The poller's only collaborator is the Jira REST API — an external HTTP
boundary. Per CLAUDE.md ("solitary is correct for ... external boundaries
we can't run"), these tests fake ``urllib.request.urlopen`` and assert the
poller's observable behaviour: the JQL it composes, token pagination, the
page cap, and the fail-without-auth-leak contract. Mirrors the harness of
``tests/test_notion_polling.py``.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from sources.jira.poller import (
    _build_jql,
    _to_jql_datetime,
    search_issues_updated_since,
)

_BASE = {"base_url": "https://acme.atlassian.net", "email": "e@example.com"}
_SEARCH_URL = "https://acme.atlassian.net/rest/api/3/search/jql"


class _FakeResp:
    """Minimal urlopen return value — a context manager with read()/status."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, _n: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False


def _install_urlopen(monkeypatch, outcomes: list) -> list[dict]:
    """Patch urlopen with a scripted sequence of outcomes.

    Each outcome is a dict (→ 200 JSON response), bytes (→ raw 200 body,
    for the non-JSON case), or an Exception instance (→ raised). Returns a
    list into which each request's parsed JSON body is recorded.
    """
    captured: list[dict] = []
    seq = list(outcomes)

    def _fake(req, timeout=None):  # noqa: ANN001, ARG001
        captured.append(json.loads(req.data.decode("utf-8")))
        outcome = seq.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, bytes):
            return _FakeResp(outcome)
        return _FakeResp(json.dumps(outcome).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    return captured


# ── _to_jql_datetime ────────────────────────────────────────────────────────


def test_to_jql_datetime_normalizes_iso8601():
    """An ISO-8601 `updated` value (T / millis / offset) → minute precision."""
    assert _to_jql_datetime("2026-05-21T10:30:45.123+0000") == "2026-05-21 10:30"


def test_to_jql_datetime_passes_through_space_form():
    assert _to_jql_datetime("2026-05-21 09:00") == "2026-05-21 09:00"


def test_to_jql_datetime_rejects_unparseable():
    with pytest.raises(ValueError, match="not a recognizable timestamp"):
        _to_jql_datetime("last tuesday")


# ── _build_jql ──────────────────────────────────────────────────────────────


def test_build_jql_scope_and_watermark():
    jql = _build_jql("project in (PROJ)", "2026-05-21T10:30:00.000+0000")
    assert jql == "(project in (PROJ)) AND updated >= '2026-05-21 10:30' ORDER BY updated ASC"


def test_build_jql_watermark_only():
    assert _build_jql(None, "2026-05-20 00:00") == (
        "updated >= '2026-05-20 00:00' ORDER BY updated ASC"
    )


def test_build_jql_empty_is_just_ordering():
    assert _build_jql(None, None) == "ORDER BY updated ASC"


# ── search_issues_updated_since ─────────────────────────────────────────────


def test_search_single_page_returns_issues_and_composes_jql(monkeypatch):
    captured = _install_urlopen(
        monkeypatch,
        [{"issues": [{"key": "PROJ-1"}, {"key": "PROJ-2"}], "isLast": True}],
    )
    result = search_issues_updated_since(
        **_BASE, token="tok", scope_jql="project = PROJ", updated_after="2026-05-20 00:00"
    )
    assert [i["key"] for i in result] == ["PROJ-1", "PROJ-2"]
    assert captured[0]["jql"] == (
        "(project = PROJ) AND updated >= '2026-05-20 00:00' ORDER BY updated ASC"
    )
    assert "nextPageToken" not in captured[0]


def test_search_paginates_with_next_page_token(monkeypatch):
    """Page 1 hands back a nextPageToken; page 2 is isLast — both accumulate,
    and page 2's request carries the token."""
    captured = _install_urlopen(
        monkeypatch,
        [
            {"issues": [{"key": "PROJ-1"}], "nextPageToken": "tok-2", "isLast": False},
            {"issues": [{"key": "PROJ-2"}], "isLast": True},
        ],
    )
    result = search_issues_updated_since(**_BASE, token="tok")
    assert [i["key"] for i in result] == ["PROJ-1", "PROJ-2"]
    assert "nextPageToken" not in captured[0]
    assert captured[1]["nextPageToken"] == "tok-2"


def test_search_stops_when_token_absent_even_without_isLast(monkeypatch):
    """A page with no nextPageToken is the last page (isLast may be omitted)."""
    _install_urlopen(monkeypatch, [{"issues": [{"key": "PROJ-1"}]}])
    result = search_issues_updated_since(**_BASE, token="tok")
    assert [i["key"] for i in result] == ["PROJ-1"]


def test_search_page_cap_raises(monkeypatch):
    """Every page claims more pages — the cap raises rather than looping."""
    monkeypatch.setattr("sources.jira.poller._MAX_PAGES", 3)
    _install_urlopen(
        monkeypatch,
        [
            {"issues": [{"key": f"PROJ-{n}"}], "nextPageToken": f"t{n}", "isLast": False}
            for n in range(10)
        ],
    )
    with pytest.raises(RuntimeError, match="exceeded"):
        search_issues_updated_since(**_BASE, token="tok")


def test_search_http_error_raises_without_auth_leak(monkeypatch):
    _install_urlopen(
        monkeypatch,
        [urllib.error.HTTPError(_SEARCH_URL, 403, "Forbidden", None, None)],
    )
    with pytest.raises(RuntimeError, match="HTTP 403") as excinfo:
        search_issues_updated_since(**_BASE, token="super-secret-token")
    # The exception must never carry the credential.
    assert "super-secret-token" not in str(excinfo.value)


def test_search_network_error_raises(monkeypatch):
    _install_urlopen(monkeypatch, [urllib.error.URLError("connection refused")])
    with pytest.raises(RuntimeError, match="network error"):
        search_issues_updated_since(**_BASE, token="tok")


def test_search_non_json_body_raises(monkeypatch):
    _install_urlopen(monkeypatch, [b"<html>not json</html>"])
    with pytest.raises(RuntimeError, match="non-JSON"):
        search_issues_updated_since(**_BASE, token="tok")


def test_search_unparseable_watermark_raises_value_error(monkeypatch):
    """A bad updated_after fails before any HTTP call — never a malformed JQL."""
    _install_urlopen(monkeypatch, [])
    with pytest.raises(ValueError, match="not a recognizable timestamp"):
        search_issues_updated_since(**_BASE, token="tok", updated_after="whenever")


def test_search_non_object_json_raises(monkeypatch):
    """A valid-JSON-but-non-object body (e.g. a bare list) → RuntimeError."""
    _install_urlopen(monkeypatch, [[1, 2, 3]])
    with pytest.raises(RuntimeError, match="non-object"):
        search_issues_updated_since(**_BASE, token="tok")
