"""Linear polling helper for Phase 1b passive ingest (#337).

Wraps the Phase 1a GraphQL client with a `completedAt`-ordered query
that returns issues completed strictly after the watermark.

Pagination uses Linear's `first` + `after` connection model. Cap at 10
pages × 50 issues = 500 per pull — large team backlogs that exceed this
should narrow the team filter or split the source config.
"""

from __future__ import annotations

_PAGE_SIZE = 50
_MAX_PAGES = 10

_LIST_ISSUES_QUERY = """
query ListCompletedIssues(
  $first: Int!
  $after: String
  $filter: IssueFilter
) {
  issues(first: $first, after: $after, filter: $filter, orderBy: updatedAt) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier
      url
      completedAt
      updatedAt
    }
  }
}
"""


def list_completed_issues(
    *,
    api_key: str,
    completed_after: str | None = None,
    team_keys: list[str] | None = None,
):
    """Return issues with ``completedAt > completed_after``.

    Each result dict has ``identifier``, ``url``, ``completedAt``,
    ``updatedAt``. Sorted ascending by ``updatedAt`` (Linear's orderBy
    keys are limited; we re-sort by ``completedAt`` client-side below
    for the watermark advance).

    ``team_keys`` (e.g. ``["BIC", "ENG"]``) restricts results via
    ``filter: {team: {key: {in: ...}}}``. ``None`` returns issues
    across all teams the API key can see.

    Raises ``RuntimeError`` on Linear GraphQL failure with a message the
    polling adapter logs without advancing the watermark.
    """
    from sources.linear.client import LinearAPIError, query

    issue_filter: dict = {"completedAt": {"null": False}}
    if completed_after:
        issue_filter["completedAt"] = {"gt": completed_after}
    if team_keys:
        issue_filter["team"] = {"key": {"in": list(team_keys)}}

    results: list[dict] = []
    after: str | None = None
    pages = 0
    while True:
        variables = {"first": _PAGE_SIZE, "after": after, "filter": issue_filter}
        try:
            data = query(
                api_key=api_key,
                document=_LIST_ISSUES_QUERY,
                variables=variables,
            )
        except LinearAPIError as exc:
            raise RuntimeError(f"Linear issue listing failed: {exc}") from exc

        issues_conn = data.get("issues") or {}
        for node in issues_conn.get("nodes") or []:
            # Filter out issues without completedAt — the GraphQL filter
            # above should already exclude these, but belt-and-suspenders
            # for the watermark math.
            if not node.get("completedAt"):
                continue
            results.append(node)
        pages += 1
        page_info = issues_conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break
        if pages >= _MAX_PAGES:
            raise RuntimeError(
                f"Linear returned more than {_MAX_PAGES * _PAGE_SIZE} issues "
                "since the watermark — narrow team_keys or shorten the watch "
                "window"
            )

    # Sort by completedAt ascending so the polling adapter can watermark
    # on the last item it ingests.
    results.sort(key=lambda d: d.get("completedAt") or "")
    return results
