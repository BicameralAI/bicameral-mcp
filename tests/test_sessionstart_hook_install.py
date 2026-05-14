"""Tests for the SessionStart hook installer (#279 Phase 1 Phase D).

Pins:
  1. The hook is installed under hooks.SessionStart in .claude/settings.json.
  2. The command always ends with `exit 0` so it can NEVER block session start.
  3. Stderr is redirected to ~/.bicameral/hook-errors.log on the operator's OS.
  4. The installer is idempotent — repeated runs produce a single entry.
  5. Existing non-bicameral SessionStart entries from other tools are preserved.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import setup_wizard


def test_session_start_command_ends_with_exit_zero_posix() -> None:
    """POSIX shape must end with `exit 0` so the hook can't block session start."""
    cmd = setup_wizard._build_session_start_command(platform="linux")
    assert cmd.rstrip().endswith("exit 0")


def test_session_start_command_ends_with_exit_zero_windows() -> None:
    cmd = setup_wizard._build_session_start_command(platform="win32")
    assert cmd.rstrip().endswith("exit 0")


def test_session_start_command_redirects_stderr_to_hook_errors_log_posix() -> None:
    """POSIX: stderr appended (>>) to ${HOME}/.bicameral/hook-errors.log."""
    cmd = setup_wizard._build_session_start_command(platform="linux")
    assert '2>>"${HOME}/.bicameral/hook-errors.log"' in cmd


def test_session_start_command_redirects_stderr_to_hook_errors_log_windows() -> None:
    cmd = setup_wizard._build_session_start_command(platform="win32")
    assert "2>>" in cmd
    assert "%USERPROFILE%" in cmd
    assert "hook-errors.log" in cmd


def test_session_start_command_invokes_sync_and_brief() -> None:
    for platform in ("linux", "darwin", "win32"):
        cmd = setup_wizard._build_session_start_command(platform=platform)
        assert "bicameral-mcp sync-and-brief" in cmd


def test_install_claude_hooks_writes_session_start_entry(tmp_path: Path) -> None:
    """The hook installer adds an entry under hooks.SessionStart matching
    the canonical SessionStart command."""
    wrote = setup_wizard._install_claude_hooks(repo_path=tmp_path)
    assert wrote is True
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    session_start = settings["hooks"]["SessionStart"]
    assert len(session_start) == 1
    assert session_start[0]["hooks"][0]["command"] == setup_wizard._BICAMERAL_SESSION_START_COMMAND


def test_install_claude_hooks_is_idempotent_for_session_start(tmp_path: Path) -> None:
    """Running the installer twice produces exactly one SessionStart entry."""
    setup_wizard._install_claude_hooks(repo_path=tmp_path)
    setup_wizard._install_claude_hooks(repo_path=tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    session_start = settings["hooks"]["SessionStart"]
    # Exactly one bicameral entry
    bic_entries = [
        e
        for e in session_start
        if any("bicameral" in h.get("command", "") or "sync-and-brief" in h.get("command", "")
               for h in e.get("hooks", []))
    ]
    assert len(bic_entries) == 1


def test_install_claude_hooks_preserves_third_party_session_start_entries(
    tmp_path: Path,
) -> None:
    """A SessionStart entry from another tool (e.g., a different MCP server)
    must survive the bicameral install."""
    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": "other-tool-hook --do-thing"}
                            ]
                        }
                    ]
                }
            }
        )
    )
    setup_wizard._install_claude_hooks(repo_path=tmp_path)
    settings = json.loads(settings_path.read_text())
    session_start = settings["hooks"]["SessionStart"]
    other_tool = [
        e
        for e in session_start
        if any("other-tool-hook" in h.get("command", "") for h in e.get("hooks", []))
    ]
    bic = [
        e
        for e in session_start
        if any(
            "bicameral" in h.get("command", "") or "sync-and-brief" in h.get("command", "")
            for h in e.get("hooks", [])
        )
    ]
    assert len(other_tool) == 1
    assert len(bic) == 1
