"""GitHub source adapter (#337 Phase 3 — active ingest).

Handles three URL shapes:
    https://github.com/<owner>/<repo>/pull/<number>      — PR + reviews + comments
    https://github.com/<owner>/<repo>/issues/<number>    — issue + comments
    https://github.com/<owner>/<repo>/commit/<sha>       — commit metadata

PR ingest pulls the PR body + reviews + comments as separate decision
proposals; downstream gap-judge classifies. Commit ingest produces a
single decision row from the commit message (useful for the
``decision:`` commit convention from #337's original capture-pipeline
sketch).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PR_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<num>\d+)(?:[/?#].*)?$"
)
_ISSUE_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/issues/(?P<num>\d+)(?:[/?#].*)?$"
)
_COMMIT_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/commit/(?P<sha>[0-9a-fA-F]{7,40})(?:[/?#].*)?$"
)


@dataclass(frozen=True)
class _ParsedURL:
    kind: str  # "pull", "issue", or "commit"
    owner: str
    repo: str
    identifier: str  # number for pull/issue, sha for commit


def parse_github_url(url: str) -> _ParsedURL:
    """Parse a GitHub URL into structured parts.

    Raises:
        ValueError: URL doesn't match any supported GitHub shape.
    """
    url = url.strip()
    for kind, rx, key in (
        ("pull", _PR_RE, "num"),
        ("issue", _ISSUE_RE, "num"),
        ("commit", _COMMIT_RE, "sha"),
    ):
        m = rx.match(url)
        if m:
            return _ParsedURL(
                kind=kind,
                owner=m.group("owner"),
                repo=m.group("repo"),
                identifier=m.group(key),
            )
    raise ValueError(
        f"not a recognized GitHub URL: {url!r}. "
        "Expected github.com/<owner>/<repo>/{pull|issues|commit}/<id>."
    )


def _collect_participants(*sources: list[dict] | dict | None) -> list[str]:
    """Best-effort participant collection across PR/issue/comment lists.

    Each source is a list of dicts with a ``user`` field, OR a single
    dict with ``user``. Login is used as identity (email rarely exposed
    on GitHub's public API).
    """
    seen: set[str] = set()
    out: list[str] = []
    for src in sources:
        if src is None:
            continue
        items = src if isinstance(src, list) else [src]
        for item in items:
            user = item.get("user") or item.get("author") or {}
            login = user.get("login")
            if login and login not in seen:
                seen.add(login)
                out.append(login)
    return out


def normalize_pr_to_payload(
    pull: dict,
    reviews: list[dict],
    comments: list[dict],
) -> dict:
    """Build the ingest payload from a PR + its reviews + its comments."""
    number = pull.get("number")
    repo_full = (pull.get("base") or {}).get("repo", {}).get("full_name") or ""
    title = pull.get("title") or f"PR #{number}"
    identifier = f"{repo_full}#PR-{number}" if repo_full else f"PR-{number}"

    decisions: list[dict] = []
    body = (pull.get("body") or "").strip()
    if body:
        decisions.append({"description": body, "title": identifier})
    for review in reviews:
        review_body = (review.get("body") or "").strip()
        if not review_body:
            continue
        state = review.get("state") or "COMMENTED"
        decisions.append(
            {
                "description": f"[{state}] {review_body}",
                "title": f"{identifier}#review-{review.get('id')}",
            }
        )
    for comment in comments:
        comment_body = (comment.get("body") or "").strip()
        if not comment_body:
            continue
        decisions.append(
            {
                "description": comment_body,
                "title": f"{identifier}#comment-{comment.get('id')}",
            }
        )

    return {
        "query": title,
        "source": "github",
        "title": identifier,
        "date": pull.get("merged_at") or pull.get("closed_at") or pull.get("updated_at") or "",
        "participants": _collect_participants([pull], reviews, comments),
        "decisions": decisions,
    }


def normalize_issue_to_payload(issue: dict, comments: list[dict]) -> dict:
    """Build the ingest payload from an issue + its comments."""
    number = issue.get("number")
    # The issue object doesn't carry repo info — derive from html_url
    # if available.
    html_url = issue.get("html_url") or ""
    repo_full = ""
    m = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)/", html_url)
    if m:
        repo_full = f"{m.group(1)}/{m.group(2)}"
    title = issue.get("title") or f"issue #{number}"
    identifier = f"{repo_full}#issue-{number}" if repo_full else f"issue-{number}"

    decisions: list[dict] = []
    body = (issue.get("body") or "").strip()
    if body:
        decisions.append({"description": body, "title": identifier})
    for comment in comments:
        comment_body = (comment.get("body") or "").strip()
        if not comment_body:
            continue
        decisions.append(
            {
                "description": comment_body,
                "title": f"{identifier}#comment-{comment.get('id')}",
            }
        )

    return {
        "query": title,
        "source": "github",
        "title": identifier,
        "date": issue.get("closed_at") or issue.get("updated_at") or "",
        "participants": _collect_participants([issue], comments),
        "decisions": decisions,
    }


def normalize_commit_to_payload(commit: dict, owner: str, repo: str) -> dict:
    """Build the ingest payload from a single commit.

    Useful for the ``decision: <text>`` commit-convention path from
    #337's original capture-pipeline sketch — operator points at the
    commit, adapter pulls the message into the ledger.
    """
    sha = commit.get("sha") or ""
    commit_info = commit.get("commit") or {}
    message = (commit_info.get("message") or "").strip()
    author = (commit_info.get("author") or {}).get("email") or ""

    identifier = f"{owner}/{repo}@{sha[:8]}" if sha else f"{owner}/{repo}@unknown"

    decisions = []
    if message:
        decisions.append({"description": message, "title": identifier})

    return {
        "query": message.splitlines()[0] if message else identifier,
        "source": "github",
        "title": identifier,
        "date": (commit_info.get("author") or {}).get("date") or "",
        "participants": [author] if author else [],
        "decisions": decisions,
    }


class GitHubAdapter:
    """SourceAdapter implementation for GitHub (active path)."""

    source_id = "github"

    def can_handle_url(self, url: str) -> bool:
        try:
            parse_github_url(url)
            return True
        except ValueError:
            return False

    def fetch_active(self, url: str) -> dict:
        parsed = parse_github_url(url)
        api_key = self._resolve_api_key()
        from sources.github.client import (
            get_commit,
            get_issue,
            get_issue_comments,
            get_pull,
            get_pull_reviews,
        )

        if parsed.kind == "pull":
            number = int(parsed.identifier)
            pull = get_pull(api_key=api_key, owner=parsed.owner, repo=parsed.repo, number=number)
            reviews = get_pull_reviews(
                api_key=api_key, owner=parsed.owner, repo=parsed.repo, number=number
            )
            comments = get_issue_comments(
                api_key=api_key, owner=parsed.owner, repo=parsed.repo, number=number
            )
            return normalize_pr_to_payload(pull, reviews, comments)

        if parsed.kind == "issue":
            number = int(parsed.identifier)
            issue = get_issue(api_key=api_key, owner=parsed.owner, repo=parsed.repo, number=number)
            comments = get_issue_comments(
                api_key=api_key, owner=parsed.owner, repo=parsed.repo, number=number
            )
            return normalize_issue_to_payload(issue, comments)

        # commit
        commit = get_commit(
            api_key=api_key, owner=parsed.owner, repo=parsed.repo, sha=parsed.identifier
        )
        return normalize_commit_to_payload(commit, parsed.owner, parsed.repo)

    def _resolve_api_key(self) -> str:
        from secrets_store import get_secret

        key = get_secret(source_id=self.source_id, key="api_key")
        if not key:
            raise RuntimeError(
                "GitHub API key not configured. Set it via:\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='github', key='api_key', value='ghp_...')\""
            )
        return key
