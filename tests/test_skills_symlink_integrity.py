"""Symlink integrity check for `.claude/skills/bicameral-*` (#357 sub-task 4).

PR #307 replaced the `.claude/skills/bicameral-*` duplicates with symlinks to
the canonical `skills/bicameral-*` source. Git stores those as mode-120000
entries on every platform, but Windows defaults to `core.symlinks=false` and
materializes them as plain text files containing the target path string
("../../skills/bicameral-preflight") instead of resolving them as symlinks.

That breakage is silent — the regular MCP test surface still passes because
nothing tries to follow the symlinks from inside the test harness. The
breakage only surfaces when Claude Code's slash-command resolver tries to
resolve `/bicameral-preflight` and finds a plain file where it expected a
symlink to a directory. This test is the early-warning gate.

CI also has a workflow-level check
(`.github/workflows/test-mcp-regression.yml` — "Assert .claude/skills/
symlinks…") that fires on every PR. This test duplicates the contract at
the pytest layer so contributors running `pytest tests/` locally see the
same failure with the same actionable error message before they push.

Setup wizard (`setup_wizard.py::_install_skills`) is NOT the right place
for this check — that path is for wheel installs where the bundled
`skills/` content is *copied* into the target repo's `.claude/skills/`.
There's no symlink at that layer; nothing to check.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

# PR #307 established 22 symlinks. Lower bound — adding more bicameral-*
# skills will push this up, removing one would be intentional and require
# updating this constant.
EXPECTED_MIN_SYMLINKS = 22


def _git_ls_files() -> list[tuple[str, str]]:
    """Return [(mode, path)] for tracked entries under .claude/skills/."""
    result = subprocess.run(
        ["git", "ls-files", "-s", ".claude/skills/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(
            f"git ls-files failed (not a git repo, or git unavailable): {result.stderr.strip()}"
        )
    entries: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        # Format: "<mode> <sha> <stage>\t<path>"
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        meta = parts[0].split()
        if len(meta) < 1:
            continue
        entries.append((meta[0], parts[1]))
    return entries


def test_skills_symlinks_tracked_as_mode_120000():
    """The git index must carry .claude/skills/bicameral-* as symlinks.

    Catches a regression where one of those symlinks was accidentally
    re-committed as a plain file — the change would compile and ship,
    but slash-command resolution would silently drift back to the old
    duplicate-and-skew problem PR #307 closed.
    """
    entries = _git_ls_files()
    symlink_entries = [(m, p) for (m, p) in entries if m == "120000"]
    assert len(symlink_entries) >= EXPECTED_MIN_SYMLINKS, (
        f"Expected at least {EXPECTED_MIN_SYMLINKS} mode-120000 symlink entries "
        f"under .claude/skills/, got {len(symlink_entries)}.\n"
        f"PR #307 (chore(skills)) established the symlink contract. A regression "
        f"here means a skill mirror has been re-committed as a plain file (or was "
        f"deleted). Inspect with:\n"
        f"  git ls-files -s .claude/skills/ | awk '$1 != \"120000\"'\n"
        f"and either restore the symlink or update {__file__}::"
        f"EXPECTED_MIN_SYMLINKS if the change is intentional."
    )


def test_skills_symlinks_materialize_on_this_clone():
    """The on-disk checkout must resolve the symlinks as real symlinks.

    Catches Windows clones where `core.symlinks=false` left the entries as
    plain text files containing the target path. The failure message
    surfaces the specific fix command — contributors should never have to
    grep for "how do I fix this" when CI tells them outright.
    """
    probe = SKILLS_DIR / "bicameral-preflight"
    assert probe.exists(), (
        f"{probe} is missing entirely. Either the .claude/skills/ scaffold "
        f"was wiped from the working tree, or `git clone` failed mid-way. "
        f"Re-clone or run `git checkout .claude/skills/`."
    )
    if probe.is_symlink():
        target = os.readlink(probe)
        assert target.startswith("../../skills/"), (
            f"{probe} resolves as a symlink but points to {target!r} — "
            f"expected something starting with '../../skills/'. Either the "
            f"symlink was edited or the canonical path moved."
        )
        return

    # Not a symlink: likely Windows with core.symlinks=false.
    content = probe.read_text().strip() if probe.is_file() else "<not a regular file>"
    if content.startswith("../../skills/"):
        pytest.fail(
            f"{probe} is a plain file containing the path string {content!r} "
            f"instead of a real symlink.\n\n"
            f"This is the Windows `core.symlinks=false` failure mode. PR #307 "
            f"established the canonical-skill-source contract via symlinks; "
            f"Windows clones must opt in to symlink materialization.\n\n"
            f"Fix:\n"
            f"  1. Set core.symlinks=true GLOBALLY (or per-clone before cloning):\n"
            f"     git config --global core.symlinks true\n"
            f"  2. Re-clone the repo, OR run inside the existing clone:\n"
            f"     git rm --cached .claude/skills/bicameral-*\n"
            f"     git checkout -- .claude/skills/\n"
            f"  3. Alternative: develop inside WSL where symlinks work natively.\n\n"
            f"See CLAUDE.md §'Canonical Skill Source' for the design rationale."
        )
    pytest.fail(
        f"{probe} is neither a symlink nor a path-string text file. "
        f"Unexpected state: {content!r}. Investigate the clone manually."
    )
