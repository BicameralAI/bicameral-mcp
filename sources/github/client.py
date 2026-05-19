"""GitHub REST API client (#337 Phase 3 — active ingest).

Mirrors the Linear/Notion clients: stdlib urllib, 15s timeout, 4 MiB
response cap, Bearer-token Authorization header, never URL.

API base: https://api.github.com. ``Accept: application/vnd.github+json``
header is preferred per GitHub's recommendation; X-GitHub-Api-Version
pinned to ``2022-11-28`` so a future GitHub API rev doesn't silently
reshape responses under us.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_API_BASE = "https://api.github.com"
_API_VERSION = "2022-11-28"
_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_PAGINATION_PAGES = 10  # 10 * 100 comments = 1000 — enough for any sensible thread


class GitHubAPIError(RuntimeError):
    """Raised for any non-recoverable GitHub API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


def _get(*, api_key: str, path: str, params: dict | None = None) -> tuple[dict | list, dict]:
    """GET ``{_API_BASE}{path}`` and return (parsed JSON, response headers).

    Headers are returned so the pagination helper can read ``Link`` for
    next-page cursors (GitHub uses Link header pagination).
    """
    url = f"{_API_BASE}{path}"
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": _API_VERSION,
        "User-Agent": "bicameral-mcp/source-github",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise GitHubAPIError(
                    f"GitHub response exceeded {_MAX_RESPONSE_BYTES} bytes",
                    status_code=resp.status,
                )
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        raise GitHubAPIError(
            f"GitHub API HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise GitHubAPIError(f"GitHub API network error: {exc.reason}") from exc

    try:
        return json.loads(raw.decode("utf-8")), resp_headers
    except json.JSONDecodeError as exc:
        raise GitHubAPIError(f"GitHub API returned non-JSON: {exc}") from exc


def get_pull(*, api_key: str, owner: str, repo: str, number: int) -> dict:
    """Fetch a single pull request."""
    data, _ = _get(api_key=api_key, path=f"/repos/{owner}/{repo}/pulls/{number}")
    if not isinstance(data, dict):
        raise GitHubAPIError(f"unexpected pull response shape: {type(data).__name__}")
    return data


def get_issue(*, api_key: str, owner: str, repo: str, number: int) -> dict:
    """Fetch a single issue (or PR; GitHub's issue endpoint covers both)."""
    data, _ = _get(api_key=api_key, path=f"/repos/{owner}/{repo}/issues/{number}")
    if not isinstance(data, dict):
        raise GitHubAPIError(f"unexpected issue response shape: {type(data).__name__}")
    return data


def get_commit(*, api_key: str, owner: str, repo: str, sha: str) -> dict:
    """Fetch a single commit with stats + file list."""
    data, _ = _get(api_key=api_key, path=f"/repos/{owner}/{repo}/commits/{sha}")
    if not isinstance(data, dict):
        raise GitHubAPIError(f"unexpected commit response shape: {type(data).__name__}")
    return data


def get_issue_comments(*, api_key: str, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch all issue/PR comments, following Link-header pagination."""
    return _paginate_list(
        api_key=api_key,
        path=f"/repos/{owner}/{repo}/issues/{number}/comments",
    )


def get_pull_reviews(*, api_key: str, owner: str, repo: str, number: int) -> list[dict]:
    """Fetch PR review records."""
    return _paginate_list(
        api_key=api_key,
        path=f"/repos/{owner}/{repo}/pulls/{number}/reviews",
    )


def _parse_link_next(link_header: str) -> str | None:
    """Extract the ``rel="next"`` URL from a GitHub Link header, or None."""
    for chunk in link_header.split(","):
        if 'rel="next"' in chunk:
            start = chunk.find("<")
            end = chunk.find(">")
            if start >= 0 and end > start:
                return chunk[start + 1 : end]
    return None


def _paginate_list(*, api_key: str, path: str) -> list[dict]:
    """Generic paginated GET — accumulates the JSON array across pages.

    Capped at ``_MAX_PAGINATION_PAGES`` * 100 records.
    """
    out: list[dict] = []
    current_path: str | None = path + "?per_page=100"
    pages = 0
    while current_path:
        if current_path.startswith(_API_BASE):
            url_suffix = current_path[len(_API_BASE) :]
        else:
            url_suffix = current_path
        data, headers = _get(api_key=api_key, path=url_suffix)
        if not isinstance(data, list):
            raise GitHubAPIError(f"expected list at {path}, got {type(data).__name__}")
        out.extend(data)
        pages += 1
        if pages >= _MAX_PAGINATION_PAGES:
            raise GitHubAPIError(
                f"{path} has more than {_MAX_PAGINATION_PAGES * 100} records — "
                "narrow the ingest scope"
            )
        link = headers.get("Link") or headers.get("link")
        if not link:
            break
        next_url = _parse_link_next(link)
        if not next_url:
            break
        current_path = next_url
    return out
