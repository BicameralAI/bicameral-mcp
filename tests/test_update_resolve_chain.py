"""Behavioral tests for handlers.update._resolve_install_command (#199).

The installer resolver picks the canonical install path in priority
order: uv > pipx > pip. uv is preferred because it ships as a single
static binary with no Python prerequisite; pipx is the established
fallback; pip is the last-resort path for venv/dev installs.
"""

from __future__ import annotations

import sys

import pytest

from handlers import update as update_module


def _make_which(uv: str | None, pipx: str | None):
    """Build a shutil.which stand-in returning the per-binary path map."""
    table = {"uv": uv, "pipx": pipx}

    def _which(name: str) -> str | None:
        return table.get(name)

    return _which


def test_resolve_uv_when_uv_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """uv on PATH wins regardless of pipx availability."""
    monkeypatch.setattr(update_module.shutil, "which", _make_which(uv="/usr/bin/uv", pipx=None))
    cmd = update_module._resolve_install_command("bicameral-mcp==1.2.3")
    assert cmd == ["uv", "tool", "install", "--force", "bicameral-mcp==1.2.3"]


def test_resolve_pipx_when_uv_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """pipx is the second-priority path when uv is absent."""
    monkeypatch.setattr(update_module.shutil, "which", _make_which(uv=None, pipx="/usr/bin/pipx"))
    cmd = update_module._resolve_install_command("bicameral-mcp==1.2.3")
    assert cmd == ["pipx", "install", "bicameral-mcp==1.2.3", "--force"]


def test_resolve_pip_when_uv_and_pipx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """pip is the last-resort fallback when neither uv nor pipx is on PATH."""
    monkeypatch.setattr(update_module.shutil, "which", _make_which(uv=None, pipx=None))
    cmd = update_module._resolve_install_command("bicameral-mcp==1.2.3")
    assert cmd[0] == sys.executable
    assert cmd[1:4] == ["-m", "pip", "install"]
    assert "bicameral-mcp==1.2.3" in cmd
    assert "--quiet" in cmd


def test_resolve_uv_wins_over_pipx_when_both_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Priority is uv > pipx > pip; both being available must not flip the
    chosen path. Locks the priority semantic, not just availability."""
    monkeypatch.setattr(
        update_module.shutil, "which", _make_which(uv="/usr/bin/uv", pipx="/usr/bin/pipx")
    )
    cmd = update_module._resolve_install_command("bicameral-mcp==1.2.3")
    assert cmd[0] == "uv"
