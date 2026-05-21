"""Thin Jira Cloud REST API v3 client (#337 Phase A).

Uses stdlib ``urllib.request`` rather than adding ``httpx`` / ``requests``
as a dependency — mirrors ``sources/linear/client.py`` and the precedent
set by ``handlers/update.py``. A single endpoint (Get issue) keeps the
surface tiny; if the Jira adapter family grows to need retries-with-
backoff or streaming, swap to ``httpx`` then.

Auth: Jira Cloud uses HTTP Basic auth — ``Authorization: Basic
base64(email:token)`` (see ``docs/vendor/jira/auth.md``).

Threat-model notes:
- The API token never appears in a URL string (Authorization header
  only) and never appears in a log line or an exception message — a
  failed request's exception carries the HTTP status and the request
  URL, never the ``Authorization`` header.
- Response size is capped at 8 MiB; an issue with megabytes of comments
  is a misuse signal — refuse rather than blow out memory.
- Network timeout is fixed at 15s — long enough for slow Jira regions,
  short enough that an operator-driven active fetch doesn't appear hung.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024  # 8 MiB

# Fields requested from Get issue. ``comment`` returns the default comment
# page inline (fields.comment.comments[]) — no separate comment fetch is
# needed for v0.
_ISSUE_FIELDS = "summary,description,status,assignee,reporter,updated,created,comment"


class JiraAPIError(RuntimeError):
    """Raised for any non-recoverable Jira API failure.

    Subclassed off ``RuntimeError`` so the ``SourceAdapter`` protocol
    contract (raises ``RuntimeError`` on network/auth failure) is
    satisfied. Carries ``status_code`` when the failure is HTTP-shaped;
    ``None`` for network-level failures (DNS, timeout, connection reset).

    The message never contains the API token or the ``Authorization``
    header — only the HTTP status and the request URL.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def _basic_auth_header(email: str, token: str) -> str:
    """Build the ``Basic <base64>`` Authorization header value.

    The encoded credential is built immediately before the request and
    never logged.
    """
    raw = f"{email}:{token}".encode()
    return "Basic " + base64.b64encode(raw).decode("ascii")


def get_issue(*, base_url: str, issue_key: str, email: str, token: str) -> dict:
    """Fetch a Jira issue via REST API v3.

    Issues ``GET {base_url}/rest/api/3/issue/{issue_key}`` with the
    decision-bearing fields (summary, description, status, assignee,
    reporter, timestamps, comments).

    Args:
        base_url: The tenant base, e.g. ``https://acme.atlassian.net``.
        issue_key: The issue key, e.g. ``PROJ-123``.
        email: The Atlassian account email (Basic-auth username).
        token: The Atlassian API token (Basic-auth password).

    Returns:
        The parsed JSON response — the Jira issue object.

    Raises:
        JiraAPIError: HTTP non-2xx, oversized response, non-JSON body, or
            any network-level failure. The message carries the status and
            URL but never the auth header.
    """
    path = f"/rest/api/3/issue/{urllib.parse.quote(issue_key, safe='')}"
    url = f"{base_url}{path}?{urllib.parse.urlencode({'fields': _ISSUE_FIELDS})}"
    headers = {
        "Authorization": _basic_auth_header(email, token),
        "Accept": "application/json",
        "User-Agent": "bicameral-mcp/source-jira",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            # Read up to cap + 1 byte; if we get the +1 the response is over-cap.
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise JiraAPIError(
                    f"Jira response exceeded {_MAX_RESPONSE_BYTES} bytes for {url}",
                    status_code=resp.status,
                )
    except urllib.error.HTTPError as exc:
        # exc.reason / exc.code are server-supplied; the request URL is
        # ours. Neither carries the Authorization header.
        raise JiraAPIError(
            f"Jira API HTTP {exc.code}: {exc.reason} for {url}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise JiraAPIError(f"Jira API network error for {url}: {exc.reason}") from exc

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise JiraAPIError(f"Jira API returned non-JSON for {url}: {exc}") from exc

    if not isinstance(parsed, dict):
        raise JiraAPIError(f"Jira API returned a non-object response for {url}")

    return parsed
