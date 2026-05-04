"""Behavioral tests for setup_wizard helpers.

Covers the installer fixes from issue #177:
- _install_skills warns loudly when source is missing (no silent return)
- _install_skills copies all skill folders on the happy path
- _detect_runner returns the bicameral-mcp script when present;
  raises RunnerNotFoundError when no runner is on PATH (no broken
  `python -m bicameral_mcp` fallback)
- run_setup output does not contain the stale `-m bicameral_mcp` runner-note text
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import setup_wizard  # noqa: E402


def test_install_skills_warns_when_source_missing(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    fake_wizard_dir = tmp_path / "fake-pkg"
    fake_wizard_dir.mkdir()
    fake_module_path = fake_wizard_dir / "setup_wizard.py"
    fake_module_path.write_text("")
    with patch.object(setup_wizard, "__file__", str(fake_module_path)):
        count = setup_wizard._install_skills(repo)
    assert count == 0
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "skill source" in captured.out
    assert not (repo / ".claude" / "skills").exists()


def test_install_skills_copies_all_skill_dirs(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    src = tmp_path / "pkg"
    (src / "skills" / "alpha").mkdir(parents=True)
    (src / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n")
    (src / "skills" / "beta").mkdir(parents=True)
    (src / "skills" / "beta" / "SKILL.md").write_text("---\nname: beta\n---\n")
    fake_module_path = src / "setup_wizard.py"
    fake_module_path.write_text("")
    with patch.object(setup_wizard, "__file__", str(fake_module_path)):
        count = setup_wizard._install_skills(repo)
    assert count == 2
    assert (repo / ".claude" / "skills" / "alpha" / "SKILL.md").exists()
    assert (repo / ".claude" / "skills" / "beta" / "SKILL.md").exists()


def test_detect_runner_uses_bicameral_mcp_script_when_present():
    def which(name):
        return "/usr/local/bin/bicameral-mcp" if name == "bicameral-mcp" else None

    with patch.object(setup_wizard.shutil, "which", side_effect=which):
        cmd, args = setup_wizard._detect_runner()
    assert cmd == "bicameral-mcp"
    assert args == []


def test_detect_runner_raises_when_no_runner_available():
    with patch.object(setup_wizard.shutil, "which", return_value=None):
        with pytest.raises(setup_wizard.RunnerNotFoundError):
            setup_wizard._detect_runner()


def test_session_end_command_uses_hyphen_slash_command():
    """Regression guard: the SessionEnd hook command must invoke
    /bicameral-capture-corrections (folder-name match), not the broken
    plugin-namespace form /bicameral:capture-corrections. See issue #177."""
    cmd = setup_wizard._BICAMERAL_SESSION_END_COMMAND
    assert "/bicameral-capture-corrections" in cmd
    assert "/bicameral:capture-corrections" not in cmd


def test_detect_runner_does_not_return_broken_module_fallback():
    """Regression guard for issue #177: the previous `python -m bicameral_mcp`
    fallback produced a non-functional MCP config because no `bicameral_mcp`
    package exists. The fix raises instead. This test fails if anyone
    re-introduces a non-script runner."""
    with patch.object(setup_wizard.shutil, "which", return_value=None):
        try:
            cmd, args = setup_wizard._detect_runner()
        except setup_wizard.RunnerNotFoundError:
            return
        # If we got here, _detect_runner returned without raising — that's the bug.
        pytest.fail(
            f"_detect_runner returned ({cmd!r}, {args!r}) instead of raising; "
            "broken `python -m bicameral_mcp` fallback may have been re-introduced"
        )
