"""Jira source adapter (#337 Phase A — active ingest).

URL -> REST API v3 fetch -> normalized ingest payload. Auth pulled from
the ``secrets_store`` under ``source_id="jira"``, keys ``"api_email"``
and ``"api_token"`` (Jira Cloud Basic auth needs both).

Jira URL form accepted (Jira Cloud only):
    https://<tenant>.atlassian.net/browse/PROJ-123
    https://<tenant>.atlassian.net/browse/PROJ-123/
    https://<tenant>.atlassian.net/browse/PROJ-123?focusedId=...
    https://<tenant>.atlassian.net/browse/PROJ-123#comment-123

Output payload shape (matches the natural-format branch of
``IngestPayload``):
    {
      "query": "<issue summary>",
      "source": "jira",
      "title": "PROJ-123",
      "date": "<fields.updated or fields.created or empty>",
      "participants": [<assignee>, <reporter>, <comment authors>],
      "decisions": [
        {"description": "<flattened description>", "title": "PROJ-123"},
        {"description": "<flattened comment body>",
         "title": "PROJ-123#comment-<id>"},
        ...
      ],
    }

In v3 the issue ``description`` and each comment ``body`` arrive as ADF
JSON, so they are run through ``flatten_adf`` before payload assembly.
Comments are ingested as separate decision proposals — the downstream
caller-LLM / gap-judge chain decides which are real decisions vs ambient
chatter. Empty / whitespace-only flattened bodies are filtered before
payload assembly so ``handle_ingest`` doesn't silently drop the row
(mirrors ``LinearAdapter``).

This is a protocol-conforming, fully-tested library module shipped one
phase ahead of its consumers (the Phase B webhook handler and the Phase E
poller) — the same situation as ``LinearAdapter`` (#420 Phase 1a, shipped
before its #433 poller). It is not an orphan: the Phase A tests exercise
it end-to-end.
"""

from __future__ import annotations

import re

from sources.jira.adf import flatten_adf

# Tenant: lowercase alphanumerics + hyphens. Key: a project prefix
# (uppercase letter then uppercase alphanumerics) + ``-`` + a number.
# Scheme/host matched case-insensitively; the key is upper-cased after.
_JIRA_URL_RE = re.compile(
    r"^https?://(?P<tenant>[a-z0-9-]+)\.atlassian\.net"
    r"/browse/(?P<key>[A-Z][A-Z0-9]+-\d+)(?:[/?#].*)?$",
    re.IGNORECASE,
)


def parse_jira_url(url: str) -> tuple[str, str]:
    """Extract ``(base_url, issue_key)`` from a Jira Cloud issue URL.

    ``base_url`` is derived solely from the validated tenant host
    (``https://<tenant>.atlassian.net``) — no user-controlled string is
    interpolated into the REST URL except the validated issue key.

    Raises:
        ValueError: URL doesn't match the Jira Cloud issue pattern.
    """
    m = _JIRA_URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"not a recognized Jira issue URL: {url!r}. "
            "Expected https://<tenant>.atlassian.net/browse/<PROJECT-NUMBER>[/...]."
        )
    tenant = m.group("tenant").lower()
    issue_key = m.group("key").upper()
    return f"https://{tenant}.atlassian.net", issue_key


def _user_token(user: object) -> str | None:
    """Resolve a participant token from a Jira user object.

    Jira often omits ``emailAddress`` (account privacy settings); fall
    back to ``displayName`` — a display name is an acceptable participant
    token and is better than dropping the contributor entirely.
    """
    if not isinstance(user, dict):
        return None
    email = user.get("emailAddress")
    if isinstance(email, str) and email.strip():
        return email
    name = user.get("displayName")
    if isinstance(name, str) and name.strip():
        return name
    return None


def _comments(issue: dict) -> list[dict]:
    """Return the inline comment list from a Get-issue response."""
    fields = issue.get("fields")
    if not isinstance(fields, dict):
        return []
    comment_page = fields.get("comment")
    if not isinstance(comment_page, dict):
        return []
    comments = comment_page.get("comments")
    if not isinstance(comments, list):
        return []
    return [c for c in comments if isinstance(c, dict)]


def _normalize_participants(issue: dict) -> list[str]:
    """Collect participant tokens — assignee, reporter, comment authors.

    Excludes authors of empty / whitespace-only comments (emoji reactions
    and "+1" comments don't make someone a decision participant).
    Assignee and reporter are always included regardless of activity —
    they own / raised the issue. De-duplicated, order-preserved.
    """
    fields = issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    seen: set[str] = set()
    out: list[str] = []

    for token in (_user_token(fields.get("assignee")), _user_token(fields.get("reporter"))):
        if token and token not in seen:
            seen.add(token)
            out.append(token)

    for comment in _comments(issue):
        if not flatten_adf(comment.get("body")).strip():
            continue
        token = _user_token(comment.get("author"))
        if token and token not in seen:
            seen.add(token)
            out.append(token)

    return out


def _normalize_decisions(issue: dict, issue_key: str) -> list[dict]:
    """Build the decisions list, filtering empty / whitespace-only bodies.

    The issue ``description`` and each comment ``body`` arrive as ADF and
    are flattened to plain text. Empty flattened bodies are dropped
    (mirrors ``LinearAdapter._normalize_decisions``).
    """
    fields = issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    decisions: list[dict] = []

    description = flatten_adf(fields.get("description")).strip()
    if description:
        decisions.append({"description": description, "title": issue_key})

    for comment in _comments(issue):
        body = flatten_adf(comment.get("body")).strip()
        if not body:
            continue
        comment_id = comment.get("id")
        comment_id = comment_id if isinstance(comment_id, str) and comment_id else ""
        decisions.append(
            {
                "description": body,
                "title": f"{issue_key}#comment-{comment_id}" if comment_id else issue_key,
            }
        )

    return decisions


def normalize_issue_to_payload(issue: dict, issue_key: str) -> dict:
    """Build the ingest payload from a Jira Get-issue response."""
    fields = issue.get("fields")
    fields = fields if isinstance(fields, dict) else {}
    summary = fields.get("summary")
    summary = summary if isinstance(summary, str) and summary.strip() else issue_key
    return {
        "query": summary,
        "source": "jira",
        "title": issue_key,
        "date": fields.get("updated") or fields.get("created") or "",
        "participants": _normalize_participants(issue),
        "decisions": _normalize_decisions(issue, issue_key),
    }


class JiraAdapter:
    """SourceAdapter implementation for Jira Cloud (active path)."""

    source_id = "jira"

    def can_handle_url(self, url: str) -> bool:
        return bool(_JIRA_URL_RE.match(url.strip()))

    def fetch_active(self, url: str) -> dict:
        base_url, issue_key = parse_jira_url(url)
        email, token = self._resolve_auth()
        from sources.jira.client import get_issue

        issue = get_issue(
            base_url=base_url,
            issue_key=issue_key,
            email=email,
            token=token,
        )
        if not isinstance(issue, dict) or not issue.get("fields"):
            raise RuntimeError(
                f"Jira returned no issue for key {issue_key!r}. "
                "Check the API token's account has Browse-projects permission "
                "for this project."
            )
        return normalize_issue_to_payload(issue, issue_key)

    def _resolve_auth(self) -> tuple[str, str]:
        """Pull the Jira account email + API token from the secrets store.

        Separate method so tests can override without monkey-patching the
        ``secrets_store`` module at import time. A missing secret raises a
        ``RuntimeError`` carrying operator-facing setup guidance — never
        the secret value itself.
        """
        from secrets_store import get_secret

        email = get_secret(source_id=self.source_id, key="api_email")
        token = get_secret(source_id=self.source_id, key="api_token")
        if not email or not token:
            raise RuntimeError(
                "Jira credentials not configured. Set both the account "
                "email and an API token via:\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='jira', key='api_email', "
                "value='you@example.com')\"\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='jira', key='api_token', value='<token>')\""
            )
        return email, token
