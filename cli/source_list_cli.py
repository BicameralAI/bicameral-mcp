"""CLI: ``bicameral-mcp source-list <source>`` — enumerate available resources.

Foundations cycle 1 (#337). Once a source is authenticated, this command
tells the operator what resources the integration can see — repos for
GitHub, folders for Google Drive. The output is the operator's pick-list
for the ``sources:`` config block.

Output formats:
  --format=table (default) — operator-readable two-column table
  --format=json           — JSON array for tooling / pipe into jq

Exit codes:
  0 — success (list rendered, possibly empty)
  1 — auth missing / unconfigured (actionable message to stderr)
  2 — source not yet supported (table omits unsupported sources)
  3 — API error (network, scope, etc.)
"""

from __future__ import annotations

import argparse
import json
import sys

_DISCOVERABLE_SOURCES = ("linear", "github", "google_drive")


def _build_argparser(subparser: argparse.ArgumentParser) -> None:
    """Wire the subcommand. Called from ``server.py``'s argparse."""
    subparser.add_argument(
        "source",
        choices=list(_DISCOVERABLE_SOURCES),
        help=(
            "Source to enumerate. Authenticated first via the appropriate "
            "auth flow (`source-auth` for OAuth-based; `put_secret` for "
            "static-token sources)."
        ),
    )
    subparser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table).",
    )


def main(args: argparse.Namespace) -> int:
    """Entry point invoked from ``server.py::_dispatch``."""
    dispatch = {
        "linear": _list_linear,
        "github": _list_github,
        "google_drive": _list_google_drive,
    }
    fn = dispatch.get(args.source)
    if fn is None:
        print(f"[source-list] source not supported: {args.source}", file=sys.stderr)
        return 2
    try:
        results, columns = fn()
    except _AuthMissing as exc:
        print(f"[source-list] {exc}", file=sys.stderr)
        return 1
    except _APIError as exc:
        print(f"[source-list] {args.source} API error: {exc}", file=sys.stderr)
        return 3
    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        _render_table(results, columns)
    return 0


class _AuthMissing(RuntimeError):
    """Auth credentials not configured for this source."""


class _APIError(RuntimeError):
    """Discovery API call failed."""


def _list_linear() -> tuple[list[dict], list[str]]:
    from secrets_store import get_secret

    key = get_secret(source_id="linear", key="api_key")
    if not key:
        raise _AuthMissing(
            "Linear API key not configured. Store it via put_secret "
            "(source_id='linear', key='api_key')."
        )
    from sources.linear.client import LinearAPIError, list_teams

    try:
        teams = list_teams(api_key=key)
    except LinearAPIError as exc:
        raise _APIError(str(exc)) from exc
    return teams, ["key", "name", "id"]


def _list_github() -> tuple[list[dict], list[str]]:
    from secrets_store import get_secret

    key = get_secret(source_id="github", key="api_key")
    if not key:
        raise _AuthMissing(
            "GitHub token not configured. Store via put_secret "
            "(source_id='github', key='api_key'). PAT needs `repo` scope; "
            "GitHub App installation tokens are auto-detected (ghs_ prefix)."
        )
    from sources.github.client import GitHubAPIError, list_repos

    try:
        repos = list_repos(api_key=key)
    except GitHubAPIError as exc:
        raise _APIError(str(exc)) from exc
    return repos, ["full_name", "private", "default_branch"]


def _list_google_drive() -> tuple[list[dict], list[str]]:
    from sources.google_drive.auth import load_credentials

    try:
        creds = load_credentials()
    except RuntimeError as exc:
        # load_credentials raises with the actionable handshake hint
        # when no token is stored — surface as auth-missing exit code.
        raise _AuthMissing(str(exc)) from exc
    from sources.google_drive.folder import list_visible_folders

    try:
        folders = list_visible_folders(creds)
    except RuntimeError as exc:
        raise _APIError(str(exc)) from exc
    return folders, ["name", "id", "owners"]


def _render_table(rows: list[dict], columns: list[str]) -> None:
    """Render rows as a fixed-width table to stdout.

    Operator-readable; intentionally not parser-friendly. Use
    ``--format=json`` for tooling pipelines.
    """
    if not rows:
        print("(no results)")
        return
    widths = {
        c: max(len(c), max((len(str(r.get(c, ""))) for r in rows), default=0)) for c in columns
    }
    header = "  ".join(c.ljust(widths[c]) for c in columns)
    sep = "  ".join("-" * widths[c] for c in columns)
    print(header)
    print(sep)
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))
