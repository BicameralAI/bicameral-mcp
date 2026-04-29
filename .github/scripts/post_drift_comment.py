"""Issue #49 — sticky PR-comment poster.

Invoked by ``.github/workflows/drift-report.yml`` after the renderer
has written a Markdown body to the path passed via ``--body``.

Behaviour:
  1. Fetch all comments on the PR (paginated).
  2. Find one carrying the HTML marker
     (``<!-- bicameral-drift-report -->``).
  3. If found: PATCH the existing comment (sticky update).
     If not:    POST a new comment.

Stateless. No external dependencies — uses stdlib ``urllib`` for
HTTPS so the workflow doesn't need to install ``requests``.

Authentication is via the ``GITHUB_TOKEN`` env var the workflow
provides automatically. The token's permissions are scoped to
``pull-requests: write`` + ``contents: read`` (set in workflow YAML),
which is the minimum needed for posting/updating PR comments.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_MARKER = "<!-- bicameral-drift-report -->"
_API = "https://api.github.com"
_PER_PAGE = 100  # GitHub's max per page for comment listings


# ── Public CLI entry ──────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Returns 0 on success or graceful no-op; 1 on hard
    failure (network, auth)."""
    args = _parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("[post_drift_comment] GITHUB_TOKEN missing — skipping")
        return 0
    body = _read_body(args.body)
    if body is None:
        print(f"[post_drift_comment] body file missing: {args.body}")
        return 0
    comments = _list_comments(args.repo, args.pr, token)
    existing = _find_existing_comment(comments)
    if existing is None:
        return _post_new(args.repo, args.pr, token, body)
    return _patch_existing(args.repo, existing, token, body)


# ── Helper functions (each ≤ 25 lines) ────────────────────────────────


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="post_drift_comment")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--body", required=True, help="path to body file")
    return parser.parse_args(argv)


def _read_body(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _find_existing_comment(comments: list[dict[str, Any]]) -> int | None:
    """Return the lowest comment ID whose body starts with the
    marker, or ``None`` if no comment matches.

    Defensive: when duplicates exist (rare race condition), prefer
    the oldest so the sticky is consistently the same comment row."""
    matching = [
        int(c["id"]) for c in comments if isinstance(c.get("body"), str) and _MARKER in c["body"]
    ]
    return min(matching) if matching else None


def _list_comments(
    repo: str,
    pr: int,
    token: str,
) -> list[dict[str, Any]]:
    """Fetch all PR comments, walking pagination via Link headers."""
    url = f"{_API}/repos/{repo}/issues/{pr}/comments?per_page={_PER_PAGE}"
    out: list[dict[str, Any]] = []
    while url:
        page, next_url = _http_get_paginated(url, token)
        out.extend(page)
        url = next_url
    return out


def _post_new(repo: str, pr: int, token: str, body: str) -> int:
    """POST a new sticky comment."""
    url = f"{_API}/repos/{repo}/issues/{pr}/comments"
    payload = json.dumps({"body": body}).encode("utf-8")
    req = _build_request(url, token, "POST", payload)
    try:
        with urlopen(req, timeout=30) as resp:
            print(f"[post_drift_comment] posted comment ({resp.status})")
        return 0
    except HTTPError as exc:
        print(f"[post_drift_comment] POST failed: {exc.code} {exc.reason}")
        return 1


def _patch_existing(
    repo: str,
    comment_id: int,
    token: str,
    body: str,
) -> int:
    """PATCH the existing sticky comment with the new body."""
    url = f"{_API}/repos/{repo}/issues/comments/{comment_id}"
    payload = json.dumps({"body": body}).encode("utf-8")
    req = _build_request(url, token, "PATCH", payload)
    try:
        with urlopen(req, timeout=30) as resp:
            print(f"[post_drift_comment] patched comment {comment_id} ({resp.status})")
        return 0
    except HTTPError as exc:
        print(f"[post_drift_comment] PATCH failed: {exc.code} {exc.reason}")
        return 1


def _build_request(
    url: str,
    token: str,
    method: str,
    payload: bytes,
) -> Request:
    """Construct an authenticated GitHub API request."""
    req = Request(url, data=payload, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", "application/json")
    return req


def _http_get_paginated(
    url: str,
    token: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of GET. Returns (page_data, next_url_or_None)."""
    req = Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        link = resp.headers.get("Link", "")
    return data, _parse_next_url(link)


def _parse_next_url(link_header: str) -> str | None:
    """Parse GitHub's Link header for the rel='next' URL, or None."""
    for part in link_header.split(","):
        if 'rel="next"' in part:
            start = part.find("<")
            end = part.find(">", start)
            if start != -1 and end != -1:
                return part[start + 1 : end]
    return None


if __name__ == "__main__":
    sys.exit(main())
