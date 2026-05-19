"""Linear source adapter (#420 Phase 1a — active ingest).

URL → GraphQL fetch → normalized ingest payload. Auth pulled from the
secrets_store under ``source_id="linear"``, key ``"api_key"``.

Linear URL forms accepted (all map to the same ``issue_identifier``,
e.g. ``"BIC-123"``):
    https://linear.app/<workspace>/issue/BIC-123
    https://linear.app/<workspace>/issue/BIC-123/<slug>
    https://linear.app/<workspace>/issue/BIC-123#comment-abc

Output payload shape (matches the natural-format branch of
``IngestPayload``):
    {
      "query": "<issue title>",
      "source": "linear",
      "title": "BIC-123",
      "date": "<completedAt or updatedAt or empty>",
      "participants": [<assignee email>, <comment author emails>],
      "decisions": [
        {"description": "<issue description>", "title": "BIC-123"},
        {"description": "<comment body>", "title": "BIC-123#comment-<id>"},
        ...
      ],
    }

Comments are ingested as separate decision proposals — the caller-LLM /
gap-judge chain downstream decides which are real decisions vs ambient
chatter. The empty-description case (issue with no body, comment is just
an emoji) is filtered before payload assembly so handle_ingest doesn't
silently drop the row.
"""

from __future__ import annotations

import re

_LINEAR_URL_RE = re.compile(
    r"^https?://linear\.app/[^/]+/issue/(?P<id>[A-Z][A-Z0-9]*-\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)

_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    identifier
    title
    description
    completedAt
    updatedAt
    state { name }
    assignee { email name }
    team { key }
    comments(first: 100) {
      nodes {
        id
        body
        createdAt
        user { email name }
      }
    }
  }
}
"""


def parse_linear_url(url: str) -> str:
    """Extract the Linear issue identifier (e.g. ``"BIC-123"``) from a URL.

    Raises:
        ValueError: URL doesn't match the Linear issue pattern.
    """
    m = _LINEAR_URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"not a recognized Linear issue URL: {url!r}. "
            "Expected https://linear.app/<workspace>/issue/<TEAM-NUMBER>[/...]."
        )
    return m.group("id").upper()


def _normalize_participants(issue: dict) -> list[str]:
    """Collect emails of meaningful contributors.

    Excludes commenters whose body is empty / whitespace-only — emoji
    reactions and "+1" comments don't make someone a decision participant
    for ledger purposes. Assignee is always included regardless of
    activity (they own the issue).
    """
    seen: set[str] = set()
    out: list[str] = []
    assignee = (issue.get("assignee") or {}).get("email")
    if assignee:
        seen.add(assignee)
        out.append(assignee)
    for comment in (issue.get("comments") or {}).get("nodes") or []:
        body = (comment.get("body") or "").strip()
        if not body:
            continue
        email = (comment.get("user") or {}).get("email")
        if email and email not in seen:
            seen.add(email)
            out.append(email)
    return out


def _normalize_decisions(issue: dict, identifier: str) -> list[dict]:
    """Build the decisions list. Filter out empty / whitespace-only bodies."""
    decisions: list[dict] = []
    description = (issue.get("description") or "").strip()
    if description:
        decisions.append({"description": description, "title": identifier})
    for comment in (issue.get("comments") or {}).get("nodes") or []:
        body = (comment.get("body") or "").strip()
        if not body:
            continue
        comment_id = comment.get("id") or ""
        decisions.append(
            {
                "description": body,
                "title": f"{identifier}#comment-{comment_id}" if comment_id else identifier,
            }
        )
    return decisions


def normalize_issue_to_payload(issue: dict, identifier: str) -> dict:
    """Build the ingest payload from a Linear issue response."""
    return {
        "query": issue.get("title") or identifier,
        "source": "linear",
        "title": identifier,
        "date": issue.get("completedAt") or issue.get("updatedAt") or "",
        "participants": _normalize_participants(issue),
        "decisions": _normalize_decisions(issue, identifier),
    }


class LinearAdapter:
    """SourceAdapter implementation for Linear (active path)."""

    source_id = "linear"

    def can_handle_url(self, url: str) -> bool:
        return bool(_LINEAR_URL_RE.match(url.strip()))

    def fetch_active(self, url: str) -> dict:
        identifier = parse_linear_url(url)
        api_key = self._resolve_api_key()
        from sources.linear.client import query as _query

        data = _query(
            api_key=api_key,
            document=_ISSUE_QUERY,
            variables={"id": identifier},
        )
        issue = data.get("issue")
        if not issue:
            raise RuntimeError(
                f"Linear returned no issue for identifier {identifier!r}. "
                "Check the API key has access to this workspace."
            )
        return normalize_issue_to_payload(issue, identifier)

    def _resolve_api_key(self) -> str:
        """Pull the Linear API key from the secrets store.

        Separate method so tests can override without monkey-patching
        the secrets_store module at import time.
        """
        from secrets_store import get_secret

        key = get_secret(source_id=self.source_id, key="api_key")
        if not key:
            raise RuntimeError(
                "Linear API key not configured. Set it via:\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='linear', key='api_key', value='lin_...')\""
            )
        return key
