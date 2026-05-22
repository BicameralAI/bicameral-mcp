"""Jira JQL poller for #337 Jira Phase E — poll-based passive ingest.

A poll alternative to the Phase B webhook receiver, for operators who
cannot host an HTTPS webhook endpoint. Wraps the Phase A REST-client
constants with a JQL bounded-search loop returning issues updated at/after
a watermark.

Structural twin of ``sources/notion/poller.py``: a *fetch* function — it
holds no watermark and calls no ingest. Watermark persistence
(``~/.bicameral/source-watermarks/``) and the ``handle_ingest`` call are
the caller's job, exactly as the Notion poller leaves them to its caller.

Targets ``POST /rest/api/3/search/jql`` — the token-based bounded-search
API. The legacy offset ``POST /rest/api/3/search`` is being removed by
Atlassian and is never called here (see
``docs/vendor/jira/rest-api-v3-issues-comments.md``).

Pagination cap: 20 pages x 100 issues = 2000 issues per pull (mirrors the
Notion poller's cap).

# TODO(#337 follow-on): wire into sync-and-brief. Phase E ships the poller
# ahead of its consumer, gated on operator demand per the scope doc
# (internal/jira-integration-scope.md Phase E).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

_PAGE_SIZE = 100
_MAX_PAGES = 20

# The decision-bearing field set — same as the Phase A Get-issue client.
_SEARCH_FIELDS = [
    "summary",
    "description",
    "status",
    "assignee",
    "reporter",
    "updated",
    "created",
    "comment",
]

# Matches the leading "YYYY-MM-DD" + ("T" or space) + "HH:MM" of a
# timestamp; seconds / milliseconds / offset (if any) are dropped — JQL's
# date comparator is minute-precision.
_TS_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")


def _to_jql_datetime(value: str) -> str:
    """Normalize a timestamp to JQL's ``yyyy-MM-dd HH:mm`` form.

    Jira's ``updated`` field arrives as ISO-8601 with ``T`` / milliseconds /
    a numeric offset (``2026-05-21T10:00:00.000+0000``); JQL's ``updated >=``
    comparator wants minute precision with a space. A value already in
    ``yyyy-MM-dd HH:mm`` form passes through unchanged.

    Raises ``ValueError`` for an unrecognizable value — the caller must not
    feed an unparseable string into a JQL query.
    """
    match = _TS_PREFIX_RE.match(value.strip())
    if not match:
        raise ValueError(
            f"updated_after {value!r} is not a recognizable timestamp; "
            "expected ISO-8601 or 'yyyy-MM-dd HH:mm'"
        )
    return f"{match.group(1)} {match.group(2)}"


def _build_jql(scope_jql: str | None, updated_after: str | None) -> str:
    """Compose the bounded-search JQL: scope + watermark + ascending sort.

    ``scope_jql`` (operator config) and the watermark clause are joined with
    ``AND``; the result is always ``ORDER BY updated ASC`` so the caller can
    persist the last ``updated`` it saw as the next watermark.
    """
    clauses: list[str] = []
    if scope_jql and scope_jql.strip():
        clauses.append(f"({scope_jql.strip()})")
    if updated_after:
        clauses.append(f"updated >= '{_to_jql_datetime(updated_after)}'")
    where = " AND ".join(clauses)
    return f"{where} ORDER BY updated ASC" if where else "ORDER BY updated ASC"


def search_issues_updated_since(
    *,
    base_url: str,
    email: str,
    token: str,
    scope_jql: str | None = None,
    updated_after: str | None = None,
    fields: list[str] | None = None,
) -> list[dict]:
    """Return Jira issues updated at/after ``updated_after``, oldest first.

    Paginates ``POST {base_url}/rest/api/3/search/jql`` using the token-based
    ``nextPageToken`` until the last page, and returns the accumulated raw
    issue dicts. The caller normalizes them (``normalize_issue_to_payload``)
    and persists its own watermark — this function holds none.

    Args:
        base_url: tenant base, e.g. ``https://acme.atlassian.net``.
        email: Atlassian account email (Basic-auth username).
        token: Atlassian API token (Basic-auth password).
        scope_jql: optional JQL scope clause, e.g. ``project in (PROJ, OPS)``
            — operator-supplied config, composed into the query as-is.
        updated_after: optional watermark; issues with ``updated`` at/after
            this are returned. ISO-8601 or ``yyyy-MM-dd HH:mm``.
        fields: issue fields to request; defaults to the decision-bearing set.

    Returns:
        The accumulated raw Jira issue objects, ascending by ``updated``.

    Raises:
        RuntimeError: any API / network / non-JSON failure, or the page cap
            being exceeded. The caller logs it and does NOT advance its
            watermark. The message carries the HTTP status + URL, never the
            auth header.
        ValueError: ``updated_after`` is not a recognizable timestamp.
    """
    from sources.jira.client import (
        _MAX_RESPONSE_BYTES,
        _REQUEST_TIMEOUT_SECONDS,
        JiraAPIError,
        _basic_auth_header,
    )

    jql = _build_jql(scope_jql, updated_after)
    url = f"{base_url}/rest/api/3/search/jql"
    headers = {
        "Authorization": _basic_auth_header(email, token),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "bicameral-mcp/source-jira-poller",
    }

    issues: list[dict] = []
    next_token: str | None = None
    pages = 0
    while True:
        body: dict = {
            "jql": jql,
            "fields": fields if fields is not None else _SEARCH_FIELDS,
            "maxResults": _PAGE_SIZE,
        }
        if next_token:
            body["nextPageToken"] = next_token
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
                raw = resp.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise JiraAPIError(
                        f"Jira search response exceeded {_MAX_RESPONSE_BYTES} bytes for {url}",
                        status_code=resp.status,
                    )
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Jira JQL search failed: HTTP {exc.code} {exc.reason} for {url}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Jira JQL search network error for {url}: {exc.reason}") from exc
        except JiraAPIError as exc:
            raise RuntimeError(f"Jira JQL search failed: {exc}") from exc

        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Jira JQL search returned non-JSON for {url}: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"Jira JQL search returned a non-object response for {url}")

        page_issues = data.get("issues")
        if isinstance(page_issues, list):
            issues.extend(item for item in page_issues if isinstance(item, dict))
        pages += 1

        next_token = data.get("nextPageToken")
        if data.get("isLast") or not next_token:
            break
        if pages >= _MAX_PAGES:
            raise RuntimeError(
                f"Jira JQL search exceeded {_MAX_PAGES * _PAGE_SIZE} issues since the "
                "watermark — narrow the scope_jql filter or shorten the poll interval"
            )

    return issues
