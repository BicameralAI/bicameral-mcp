#!/usr/bin/env python3
"""Governance boundary guard.

Keeps local-process and sibling-tool artifacts out of commits. The boundary is
defined by the tracked sibling registry (``docs/governance/SIBLINGS.md``) plus a
built-in default floor of common agent-scratch roots.

By default (pre-commit / CI) this checks only what a change *introduces* — staged
files and the PR diff — matching the proposal's "is staged" contract, so it does
not retroactively fail on pre-existing tracked files. Use ``--audit`` to scan the
entire tracked tree for existing leaks.

Dependency-free (stdlib only) so product repos need no extra tooling.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "docs" / "governance" / "SIBLINGS.md"

# Built-in floor: enforced even if the registry is missing or incomplete, so an
# unregistered tool still cannot leak.
#
# NOTE: .claude/ and CLAUDE.md are intentionally tracked in this repo (symlinks
# to skills/ and agent instructions respectively). The sibling registry covers
# .claude/worktrees/ for the untouched scratch dir. See SIBLINGS.md.
DEFAULT_FLOOR = [
    ".qor/",
    ".agent/",
    ".agents/",
    ".failsafe/",
    ".cursor/",
    ".windsurf/",
    ".bicameral/",
    "GEMINI.md",
    "COPILOT.md",
    "CURSOR.md",
    "RUN_SUMMARY.md",
    "plan-*.md",
]

# The only paths commit-permitted under docs/governance/.
GOVERNANCE_ALLOWLIST = {
    "docs/governance/BOUNDARY.md",
    "docs/governance/SIBLINGS.md",
    "docs/governance/README.md",
    "docs/governance/PIN.json",
    "docs/governance/compliance-stance-matrix.md",
    "docs/governance/doctrine-deterministic-governance.md",
}


def git_lines(args: list[str]) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def registry_roots() -> list[str]:
    """Parse the Root(s) column of the registry table in SIBLINGS.md."""
    roots: list[str] = []
    if not REGISTRY.exists():
        return roots
    in_registry = False
    for line in REGISTRY.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## Registry"):
            in_registry = True
            continue
        if in_registry:
            if stripped.startswith("## "):
                break
            if not stripped.startswith("|"):
                continue
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if len(cells) < 2 or cells[0].lower() in ("sibling", ""):
                continue
            if set(cells[0]) <= set("-: "):  # table separator row
                continue
            # cells[1] is Root(s); collect every `code-span` token.
            token = ""
            in_tick = False
            for ch in cells[1]:
                if ch == "`":
                    if in_tick and token:
                        roots.append(token.strip())
                    token = ""
                    in_tick = not in_tick
                elif in_tick:
                    token += ch
    return roots


def all_roots() -> list[str]:
    seen: list[str] = []
    for root in [*DEFAULT_FLOOR, *registry_roots()]:
        if root and root not in seen:
            seen.append(root)
    return seen


def match_root(path: str, root: str) -> bool:
    if root.endswith("/"):
        return path == root.rstrip("/") or path.startswith(root)
    if "*" in root:
        return fnmatch.fnmatch(path, root) or fnmatch.fnmatch(Path(path).name, root)
    return path == root


def forbidding_root(path: str, roots: list[str]) -> str | None:
    if path in GOVERNANCE_ALLOWLIST:
        return None
    for root in roots:
        if match_root(path, root):
            return root
    return None


def candidate_paths() -> list[str]:
    staged = git_lines(["diff", "--cached", "--name-only", "--diff-filter=ACMRT"])
    base = os.environ.get("GITHUB_BASE_REF", "main")
    pr = git_lines(["diff", "--name-only", "--diff-filter=ACMRT", f"origin/{base}...HEAD"])
    return sorted(set(staged) | set(pr))


def gitignore_lines() -> list[str]:
    path = ROOT / ".gitignore"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def is_covered_by_gitignore(root: str, lines: list[str]) -> bool:
    rs = root.rstrip("/").lstrip("/")
    for line in lines:
        ls = line.rstrip("/").lstrip("/")
        if not ls:
            continue
        if rs == ls or rs.startswith(ls + "/"):
            return True
        if "*" in ls and fnmatch.fnmatch(rs, ls):
            return True
    return False


def check_registry_gitignore_agreement() -> list[str]:
    """Rule #4: every registered/floor root must be gitignored (un-committable)."""
    lines = gitignore_lines()
    problems = []
    for root in [*DEFAULT_FLOOR, *registry_roots()]:
        if not is_covered_by_gitignore(root, lines):
            problems.append(f"registered sibling root not covered by .gitignore: {root}")
    return problems


def stray_governance_files(paths: list[str]) -> list[str]:
    problems = []
    for path in paths:
        if path.startswith("docs/governance/") and path not in GOVERNANCE_ALLOWLIST:
            problems.append(f"non-allowlisted file under docs/governance/: {path}")
    return problems


def main() -> int:
    audit = "--audit" in sys.argv[1:]
    roots = all_roots()

    if audit:
        paths = git_lines(["ls-files"])
        scope = "tracked tree (audit)"
    else:
        paths = candidate_paths()
        scope = "staged + PR diff"

    problems: list[str] = []
    for path in paths:
        root = forbidding_root(path, roots)
        if root is not None:
            problems.append(f"local/sibling artifact must not be committed: {path}  (matches `{root}`)")

    problems.extend(stray_governance_files(paths))
    problems.extend(check_registry_gitignore_agreement())

    if problems:
        print(f"Governance boundary check failed ({scope}):")
        for problem in problems:
            print(f"  {problem}")
        print(
            "\nLocal-process and sibling-tool artifacts stay local and gitignored. "
            "See docs/governance/BOUNDARY.md and docs/governance/SIBLINGS.md."
        )
        return 1

    print(f"Governance boundary check passed ({scope}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
