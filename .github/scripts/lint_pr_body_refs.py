"""Issue #114 Phase 1 — PR-body refs lint.

Walks a PR body looking for `#NUMBER` tokens. For each token, classifies as:
  - structured (under a `## Linked issues` section OR preceded by
    a recognised keyword: Closes/Fixes/Resolves/Refs/Related/See)
  - bare (warning emitted)

Always returns exit 0 — advisory check, never blocks merge. The CI
workflow surfaces warnings via stderr; reviewers can act on them
manually.

SECURITY-CRITICAL: this script is the receiving end of the CI
workflow's ``--from-env PR_BODY`` invocation. The PR body is
contributor-editable text that flows directly from
``${{ github.event.pull_request.body }}`` through an environment
variable into ``os.environ[NAME]`` — no Bash interpreter in the
path. An earlier draft of the workflow used ``echo "$PR_BODY" >
/tmp/file`` which exposed OWASP A03 command-substitution injection
(`$(cmd)` expanded inside Bash double quotes). The ``--from-env``
flag is the safe alternative; never restore the echo pattern.

Stdlib only — no external deps.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from collections.abc import Iterable

_NUMBER_TOKEN_RE = re.compile(r"#(\d+)")
_KEYWORDS = (
    "closes",
    "closed",
    "fixes",
    "fixed",
    "resolves",
    "resolved",
    "refs",
    "related to",
    "related",
    "see",
)
_LINKED_ISSUES_HEADING_RE = re.compile(
    r"^\s*#{1,6}\s+linked\s+issues?\s*$",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class Warning:
    """One bare-mention warning."""

    line: int  # 1-indexed
    number: int  # the issue number


# ── Public entry (≤ 30 LOC) ──────────────────────────────────────────


def lint_pr_body(body: str) -> list[Warning]:
    """Walk a PR body's lines, classify each ``#NUMBER`` token, return
    warnings for bare mentions. Pure function — no IO."""
    warnings: list[Warning] = []
    in_linked_section = False
    for lineno, line in enumerate(body.splitlines(), start=1):
        if _LINKED_ISSUES_HEADING_RE.match(line):
            in_linked_section = True
            continue
        if in_linked_section and _is_other_heading(line):
            in_linked_section = False
        if in_linked_section:
            continue
        for match in _NUMBER_TOKEN_RE.finditer(line):
            number = int(match.group(1))
            if not _has_preceding_keyword(line, match.start()):
                warnings.append(Warning(line=lineno, number=number))
    return warnings


# ── Helpers (each ≤ 20 LOC) ──────────────────────────────────────────


def _is_other_heading(line: str) -> bool:
    """Markdown heading that closes the linked-issues section."""
    return bool(re.match(r"^\s*#{1,6}\s+", line))


def _has_preceding_keyword(line: str, token_start: int) -> bool:
    """Return True when the `#NUMBER` token at ``token_start`` is
    preceded on the same line by one of the recognised
    issue-link keywords (case-insensitive). Looks back up to 32
    characters for a keyword match."""
    prefix = line[:token_start].lower()
    return any(prefix.rstrip().endswith(kw) for kw in _KEYWORDS)


def _emit_warnings(warnings: Iterable[Warning], out=None) -> None:
    """Print each warning to stderr in actionable form. ``out`` is
    resolved at call-time (default ``sys.stderr``) so test harnesses
    that replace ``sys.stderr`` (pytest capsys) capture the output
    correctly — capturing the default at function-def time would
    bind a stale reference."""
    target = out if out is not None else sys.stderr
    for w in warnings:
        print(
            f"warning: bare '#{w.number}' on line {w.line} — wrap with "
            f"'Closes #{w.number}' / 'Refs #{w.number}', or move to a "
            "'Linked issues' section",
            file=target,
        )


# ── CLI entry (≤ 25 LOC) ─────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Body source — exactly one of:
       --body <file>      — read PR body from file (local dev / tests)
       --from-env <NAME>  — read PR body from env var (CI; security-critical
                            to avoid Bash shell interpolation of contributor-
                            controlled text — see module docstring)

    Always returns 0 (advisory check)."""
    args = _parse_args(argv)
    body = _read_body(args)
    if body is None:
        return 0
    _emit_warnings(lint_pr_body(body))
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lint_pr_body_refs")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--body", help="path to PR body file")
    group.add_argument(
        "--from-env",
        metavar="NAME",
        help="environment variable to read PR body from (CI safe; no shell)",
    )
    return parser.parse_args(argv)


def _read_body(args: argparse.Namespace) -> str | None:
    """Read the PR body from whichever source the args specified.
    Returns None when the source is missing — script exits 0 silently
    rather than failing loudly (advisory)."""
    if args.body:
        try:
            with open(args.body, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return None
    return os.environ.get(args.from_env)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
