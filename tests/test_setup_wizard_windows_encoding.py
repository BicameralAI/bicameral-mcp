"""Behavioral tests for setup_wizard._ensure_utf8_stdout (#199).

Three banner blocks in setup_wizard.py (run_setup, run_config_wizard,
run_reset_wizard) print box-drawing characters (┌─┐│└┘) that crash
under Windows' default cp1252 console codepage with UnicodeEncodeError.
The fix is a small helper that reconfigures stdout/stderr to UTF-8 on
win32 only; these tests pin the per-platform behavior so future drift
trips a regression guard.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import setup_wizard


class _FakeStream:
    """Stand-in for sys.stdout/sys.stderr that records reconfigure calls."""

    def __init__(self, has_reconfigure: bool = True, raise_on_reconfigure: Exception | None = None):
        self._has_reconfigure = has_reconfigure
        self._raise = raise_on_reconfigure
        self.reconfigure_calls: list[dict] = []
        if has_reconfigure:
            self.reconfigure = self._reconfigure  # type: ignore[method-assign]

    def _reconfigure(self, **kwargs) -> None:
        self.reconfigure_calls.append(kwargs)
        if self._raise is not None:
            raise self._raise


def test_ensure_utf8_stdout_reconfigures_on_win32() -> None:
    """On Windows, the helper calls reconfigure(encoding='utf-8',
    errors='replace') on both stdout and stderr."""
    fake_out = _FakeStream()
    fake_err = _FakeStream()
    with (
        patch.object(setup_wizard.sys, "stdout", fake_out),
        patch.object(setup_wizard.sys, "stderr", fake_err),
    ):
        setup_wizard._ensure_utf8_stdout(platform="win32")
    assert fake_out.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert fake_err.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_ensure_utf8_stdout_noop_on_posix(platform: str) -> None:
    """POSIX platforms inherit utf-8 from locale; no reconfigure call."""
    fake_out = _FakeStream()
    fake_err = _FakeStream()
    with (
        patch.object(setup_wizard.sys, "stdout", fake_out),
        patch.object(setup_wizard.sys, "stderr", fake_err),
    ):
        setup_wizard._ensure_utf8_stdout(platform=platform)
    assert fake_out.reconfigure_calls == []
    assert fake_err.reconfigure_calls == []


def test_ensure_utf8_stdout_silent_when_reconfigure_missing() -> None:
    """Helper must not raise when stdout lacks .reconfigure (e.g.
    captured-output streams in some test runners)."""
    fake_out = _FakeStream(has_reconfigure=False)
    fake_err = _FakeStream(has_reconfigure=False)
    with (
        patch.object(setup_wizard.sys, "stdout", fake_out),
        patch.object(setup_wizard.sys, "stderr", fake_err),
    ):
        setup_wizard._ensure_utf8_stdout(platform="win32")  # must not raise


def test_ensure_utf8_stdout_silent_on_oserror() -> None:
    """Helper must swallow OSError from reconfigure (no-op fallback)."""
    fake_out = _FakeStream(raise_on_reconfigure=OSError("not seekable"))
    fake_err = _FakeStream(raise_on_reconfigure=OSError("not seekable"))
    with (
        patch.object(setup_wizard.sys, "stdout", fake_out),
        patch.object(setup_wizard.sys, "stderr", fake_err),
    ):
        setup_wizard._ensure_utf8_stdout(platform="win32")  # must not raise


def test_ensure_utf8_stdout_silent_on_valueerror() -> None:
    """Helper must swallow ValueError from reconfigure too."""
    fake_out = _FakeStream(raise_on_reconfigure=ValueError("bad encoding"))
    fake_err = _FakeStream(raise_on_reconfigure=ValueError("bad encoding"))
    with (
        patch.object(setup_wizard.sys, "stdout", fake_out),
        patch.object(setup_wizard.sys, "stderr", fake_err),
    ):
        setup_wizard._ensure_utf8_stdout(platform="win32")  # must not raise


def test_ensure_utf8_stdout_default_platform_reads_sys_platform() -> None:
    """When platform=None (production callers), helper reads sys.platform."""
    fake_out = _FakeStream()
    fake_err = _FakeStream()
    with (
        patch.object(setup_wizard.sys, "stdout", fake_out),
        patch.object(setup_wizard.sys, "stderr", fake_err),
        patch.object(setup_wizard.sys, "platform", "win32"),
    ):
        setup_wizard._ensure_utf8_stdout()
    assert fake_out.reconfigure_calls == [{"encoding": "utf-8", "errors": "replace"}]
