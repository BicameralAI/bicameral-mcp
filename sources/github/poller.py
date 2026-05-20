"""GitHub polling helper for Phase 3b passive ingest (#337).

Enumerates pull requests in a configured repo that were updated after
the watermark and have ``merged_at`` set (i.e. actually merged, not just
closed without merge).

Uses the existing Phase 3 GitHub REST client's pagination helper
(``_paginate_list``) so the rate-limit handling, response cap, and
Link-header cursor are shared with the active-ingest path.

Pagination cap is the same as the active-ingest path: 10 pages × 100
PRs = 1000 per cycle.
"""

from __future__ import annotations


def list_merged_pulls_since(
    *,
    api_key: str,
    owner: str,
    repo: str,
    updated_after: str | None = None,
):
    """Return PRs in ``owner/repo`` updated after ``updated_after``, filtered
    to those with ``merged_at`` set.

    Each result is the raw GitHub PR object (the polling adapter pulls
    ``html_url`` and ``merged_at`` from it).

    Sorted ascending by ``updated_at`` (GitHub's native ordering when
    sort=updated + direction=asc).

    ``updated_after`` is an ISO 8601 timestamp string. GitHub's
    ``pulls?since=<ts>`` filter is applied via _paginate_list with a
    custom path.

    Raises ``RuntimeError`` on API failure with a message the polling
    adapter logs without advancing the watermark.
    """
    from sources.github.client import GitHubAPIError

    base_path = f"/repos/{owner}/{repo}/pulls"
    # _paginate_list always appends `?per_page=100`; pass our other params
    # via `params=` so the helper composes the query string correctly.
    params: dict[str, str] = {"state": "closed", "sort": "updated", "direction": "asc"}
    if updated_after:
        params["since"] = updated_after

    try:
        pulls = _list_pulls_manual(api_key=api_key, base_path=base_path, params=params)
    except GitHubAPIError as exc:
        raise RuntimeError(f"GitHub pulls listing failed: {exc}") from exc

    # GitHub state=closed includes both merged and abandoned; filter to
    # actually-merged.
    return [p for p in pulls if p.get("merged_at")]


def _list_pulls_manual(*, api_key: str, base_path: str, params: dict) -> list[dict]:
    """Paginated GET keeping our query params alongside Link-header pagination."""
    import urllib.parse

    from sources.github.client import _MAX_PAGINATION_PAGES, GitHubAPIError, _get, _parse_link_next

    full_params = {**params, "per_page": "100"}
    url_suffix = base_path + "?" + urllib.parse.urlencode(full_params)
    out: list[dict] = []
    pages = 0
    while True:
        data, headers = _get(api_key=api_key, path=url_suffix)
        if not isinstance(data, list):
            raise GitHubAPIError(f"expected list at {base_path}, got {type(data).__name__}")
        out.extend(data)
        pages += 1
        if pages >= _MAX_PAGINATION_PAGES:
            raise GitHubAPIError(
                f"{base_path} has more than {_MAX_PAGINATION_PAGES * 100} PRs "
                "since the watermark — narrow the repo or shorten the watch window"
            )
        link = headers.get("Link") or headers.get("link")
        if not link:
            break
        next_url = _parse_link_next(link)
        if not next_url:
            break
        # _get re-prepends _API_BASE if the path starts with /; strip the
        # base from the absolute URL the Link header returned so _get
        # builds the correct request.
        from sources.github.client import _API_BASE

        if next_url.startswith(_API_BASE):
            url_suffix = next_url[len(_API_BASE) :]
        else:
            url_suffix = next_url
    return out
