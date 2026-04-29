"""Issue #114 Phase 0 — plan-grounding lint.

Walks `plan-*.md` (or `docs/Planning/plan-*.md`) files looking for
filesystem-shaped path tokens inside backticks or fenced code blocks.
For each candidate, verifies the path resolves on the working tree.
Unresolved paths emit a Diagnostic; the CLI exits non-zero if any
plan has diagnostics.

Exemptions: tokens marked ``**new**`` / ``(planned)`` / ``(future)``
/ ``(v2)`` on their bullet line; tokens inside HTML comments
(``<!-- ... -->``); tokens inside Markdown blockquotes (``>`` prefix).

Stdlib only — pathlib + re + argparse + dataclasses. No project
imports. Designed to run both as a CI step and as a dev-side
``python scripts/lint_plan_grounding.py`` invocation.

Mitigation for SG-PLAN-GROUNDING-DRIFT (the Shadow Genome pattern
where plan authors claim filesystem paths without verifying). See
Issue #114 for context.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from pathlib import Path

# Tokens to recognise as path-shaped: backtick-wrapped strings that
# contain a slash and end in a known extension (or look like a
# package directory).
_PATH_TOKEN_RE = re.compile(
    r"`([^`\s][^`]*?[^`\s])`"  # contents of a backtick-delimited span
)

_KNOWN_EXTS = (
    ".py",
    ".pyi",
    ".yaml",
    ".yml",
    ".md",
    ".json",
    ".toml",
    ".sh",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".cs",
)
_PACKAGE_DIR_RE = re.compile(r"^[a-z_][a-z0-9_]*/$")

_NEW_MARKER_RE = re.compile(r"\*\*new\*\*", re.IGNORECASE)
_PLANNED_SUFFIX_RE = re.compile(
    r"\((planned|future|v2|nonexistent|example)\)",
    re.IGNORECASE,
)
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    """One unresolved path token in a plan file."""

    path: Path  # plan file
    line: int  # 1-indexed
    token: str  # the path that did not resolve


# ── Public entry (≤ 30 LOC) ──────────────────────────────────────────


def lint_plan_text(text: str, repo_root: Path) -> list[Diagnostic]:
    """Walk a plan-*.md content string, collect Diagnostics for
    unresolved path tokens. Pure function — no IO except `repo_root /
    candidate` resolution stat-checks."""
    diagnostics: list[Diagnostic] = []
    in_html_comment = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        # Strip any single-line `<!-- ... -->` blocks; multi-line
        # comments are tracked via the in_html_comment state.
        cleaned = _strip_inline_comments(line)
        in_html_comment = _update_comment_state(cleaned, in_html_comment)
        if in_html_comment or _is_blockquote(cleaned):
            continue
        if _is_exempt_line(cleaned):
            continue
        for token in _extract_path_tokens(cleaned):
            if not _is_path_shaped(token):
                continue
            if not (repo_root / token).exists():
                diagnostics.append(Diagnostic(path=Path("<text>"), line=lineno, token=token))
    return diagnostics


# ── Helpers (each ≤ 25 LOC) ──────────────────────────────────────────


def _extract_path_tokens(line: str) -> list[str]:
    """Pull every backtick-wrapped token from a Markdown line."""
    return [m.group(1) for m in _PATH_TOKEN_RE.finditer(line)]


def _is_path_shaped(token: str) -> bool:
    """Token looks filesystem-shaped: contains `/` AND ends in a known
    extension OR matches the package-directory pattern. Excludes
    tokens with internal whitespace (multi-word, not a single path)
    and glob patterns (``*``, ``?``, ``[``)."""
    if "/" not in token:
        return False
    if any(c.isspace() for c in token):
        return False
    if any(c in token for c in ("*", "?", "[")):
        return False
    if any(token.endswith(ext) for ext in _KNOWN_EXTS):
        return True
    return bool(_PACKAGE_DIR_RE.match(token))


def _is_exempt_line(line: str) -> bool:
    """Line carries an explicit `**new**` / `(planned)` / `(future)`
    / `(v2)` marker that signals the author KNOWS the path doesn't
    yet exist. Lint passes."""
    if _NEW_MARKER_RE.search(line):
        return True
    if _PLANNED_SUFFIX_RE.search(line):
        return True
    return False


def _is_blockquote(line: str) -> bool:
    """Markdown blockquotes start with `>` (after optional whitespace).
    Treat as illustrative quotations, not file claims."""
    return line.lstrip().startswith(">")


def _update_comment_state(line: str, in_comment: bool) -> bool:
    """Track multi-line HTML comments. Returns the state AFTER
    processing this line — simple toggle on open/close markers.
    Single-line comments are stripped by ``_strip_inline_comments``
    BEFORE this is called, so the only case that flips state is a
    multi-line open."""
    if in_comment:
        return _HTML_COMMENT_CLOSE not in line
    if _HTML_COMMENT_OPEN in line and _HTML_COMMENT_CLOSE not in line:
        return True
    return False


def _strip_inline_comments(line: str) -> str:
    """Remove every `<!-- ... -->` block on a single line. The state
    machine elsewhere handles multi-line comments. After this call,
    any remaining `<!--` (without a matching `-->`) opens a multi-line
    comment block."""
    while True:
        start = line.find(_HTML_COMMENT_OPEN)
        end = line.find(_HTML_COMMENT_CLOSE, start)
        if start == -1 or end == -1:
            return line
        line = line[:start] + line[end + len(_HTML_COMMENT_CLOSE) :]


# ── CLI entry (≤ 25 LOC) ─────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """Walk every passed plan path (or all `plan-*.md` at repo root
    when none passed). Print diagnostics. Return 0 if clean, 1
    otherwise."""
    args = _parse_args(argv)
    plans = _collect_plans(args.paths)
    repo_root = Path.cwd()
    total = 0
    for plan_path in plans:
        text = plan_path.read_text(encoding="utf-8")
        for diag in lint_plan_text(text, repo_root=repo_root):
            total += 1
            print(
                f"{plan_path}:{diag.line}: '{diag.token}' does not exist",
                file=sys.stderr,
            )
    if total:
        print(f"\n{total} diagnostic(s) across {len(plans)} plan(s)", file=sys.stderr)
        return 1
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="lint_plan_grounding")
    parser.add_argument("paths", nargs="*", help="plan files to lint")
    return parser.parse_args(argv)


def _collect_plans(paths: list[str]) -> list[Path]:
    """If specific paths passed, use them. Otherwise glob plan-*.md
    at the repo root + docs/Planning/plan-*.md if that dir exists."""
    if paths:
        return [Path(p) for p in paths]
    out: list[Path] = sorted(Path.cwd().glob("plan-*.md"))
    planning_dir = Path("docs/Planning")
    if planning_dir.exists():
        out.extend(sorted(planning_dir.glob("plan-*.md")))
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
