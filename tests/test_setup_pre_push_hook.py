"""Issue #48 Phase 1 — pre-push hook installer tests.

Pure unit tests on ``setup_wizard._install_git_pre_push_hook``. Uses
``tmp_path`` to set up a fake git repo so we don't pollute the
working tree. No subprocess, no real git operations.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from setup_wizard import _install_git_pre_push_hook


def _make_git_repo(root: Path) -> Path:
    """Create a minimal `.git/` directory structure to mimic a git
    repo. ``_install_git_pre_push_hook`` only needs ``.git/`` to exist
    via ``_find_git_root``."""
    git_dir = root / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    return root


def test_install_writes_hook_in_fresh_repo(tmp_path: Path) -> None:
    """Fresh repo, no existing hook → installer writes the file with
    the bicameral marker and returns True."""
    repo = _make_git_repo(tmp_path)
    written = _install_git_pre_push_hook(repo)
    assert written is True
    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.exists()
    body = hook.read_text()
    assert "bicameral" in body
    assert "branch-scan" in body  # the actual command we want


def test_install_is_idempotent_when_already_bicameral(tmp_path: Path) -> None:
    """Install once, install twice → second call returns False; file
    content is unchanged."""
    repo = _make_git_repo(tmp_path)
    _install_git_pre_push_hook(repo)
    first_body = (repo / ".git" / "hooks" / "pre-push").read_text()
    written = _install_git_pre_push_hook(repo)
    assert written is False
    second_body = (repo / ".git" / "hooks" / "pre-push").read_text()
    assert first_body == second_body


def test_install_appends_when_existing_hook_lacks_bicameral(tmp_path: Path) -> None:
    """Existing pre-push hook without bicameral content → append, not
    overwrite. Both the prior content and the bicameral block survive."""
    repo = _make_git_repo(tmp_path)
    hook_dir = repo / ".git" / "hooks"
    hook_dir.mkdir(parents=True, exist_ok=True)
    stub = "#!/bin/sh\n# user's existing hook\necho 'pre-push'\n"
    (hook_dir / "pre-push").write_text(stub)
    written = _install_git_pre_push_hook(repo)
    assert written is True
    body = (hook_dir / "pre-push").read_text()
    assert "user's existing hook" in body
    assert "bicameral" in body


def test_install_returns_false_when_no_git_root(tmp_path: Path) -> None:
    """Path that's not inside a git repo → returns False, writes
    nothing. Mirrors ``_install_git_post_commit_hook``'s behavior."""
    not_a_repo = tmp_path / "plain_dir"
    not_a_repo.mkdir()
    written = _install_git_pre_push_hook(not_a_repo)
    assert written is False
    assert not (not_a_repo / ".git" / "hooks" / "pre-push").exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file-mode bits don't apply on Windows",
)
def test_install_sets_executable_bit(tmp_path: Path) -> None:
    """Installed hook is executable (chmod 0o755). Skipped on Windows
    where the chmod call is a no-op for x-bit semantics."""
    repo = _make_git_repo(tmp_path)
    _install_git_pre_push_hook(repo)
    hook = repo / ".git" / "hooks" / "pre-push"
    mode = hook.stat().st_mode
    # Owner must have execute; world-readable acceptable
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IRUSR
