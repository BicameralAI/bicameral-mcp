"""Behavioral tests for the OS-aware SessionEnd hook command rendering
in `setup_wizard._build_session_end_command` (#200 Phase 1).

The wizard now branches the rendered command shape on `sys.platform`
(or an explicit `platform` arg for test isolation): POSIX systems get
the bash-style `[ -d ... ] && [ -z ... ] && ... || true` shape using
`python` (not `python3` — modern distros symlink, Windows MinGW lacks
`python3`); Windows gets a cmd.exe-compatible shape using `if exist
.bicameral if not defined ... (set ... && python ...\\hooks\\...)`.

These tests pin the per-platform shape so future drift (e.g. someone
re-introducing `python3` for Linux compatibility) trips the regression
guard before it ships to a Windows user.
"""

from __future__ import annotations

import sys

import setup_wizard


def test_session_end_command_posix_uses_python3_not_python() -> None:
    """POSIX (Linux/macOS) uses python3 — Ubuntu/Debian/RHEL/Fedora
    install python3 by default; `python` is NOT a default symlink and
    requires `python-is-python3` (Ubuntu) or equivalent. python3 is
    the only reliable cross-distro POSIX choice."""
    cmd = setup_wizard._build_session_end_command(platform="linux")
    assert "python3 scripts/hooks/session_end_queue_writer.py" in cmd


def test_session_end_command_darwin_matches_posix_shape() -> None:
    linux_cmd = setup_wizard._build_session_end_command(platform="linux")
    darwin_cmd = setup_wizard._build_session_end_command(platform="darwin")
    assert linux_cmd == darwin_cmd


def test_session_end_command_win32_uses_cmd_exe_shape() -> None:
    cmd = setup_wizard._build_session_end_command(platform="win32")
    assert "if exist .bicameral" in cmd
    assert "if not defined BICAMERAL_SESSION_END_RUNNING" in cmd
    assert "set BICAMERAL_SESSION_END_RUNNING=1" in cmd
    assert "python scripts\\hooks\\session_end_queue_writer.py" in cmd
    assert "[ -d" not in cmd
    assert "[ -z" not in cmd
    assert "|| true" not in cmd


def test_session_end_command_no_platform_arg_uses_sys_platform() -> None:
    explicit = setup_wizard._build_session_end_command(platform=sys.platform)
    default = setup_wizard._build_session_end_command()
    assert explicit == default
