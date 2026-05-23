"""Per-test daemon-subprocess fixture for Phase 2c-4+ boundary tests.

Each test gets its own logical daemon — own socket, own descriptor, own
tmp dir, own subprocess. Per Fowler's [test pyramid advisory of 2026-05-22]
this fixture exists so handlers can be exercised across a true process
boundary; the cost (~8s subprocess spawn) is acceptable for boundary tests
because they're rare. When the per-test cost becomes painful, swap the
fixture body for an ObjectPool of pre-warmed daemons without touching any
test.

Usage::

    from tests._daemon_fixture import daemon_subprocess, short_state_dir

    async def test_history_through_daemon(daemon_subprocess):
        # daemon_subprocess.socket_path is bound to a real running daemon.
        ...

The fixtures live in a non-``conftest.py`` module so they're explicitly
opt-in — most tests don't need a daemon and shouldn't pay the spawn cost.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from daemon.process import DaemonDescriptor, is_alive, spawn, stop


@pytest.fixture
def short_state_dir():
    """A short-path tmp dir for the daemon's socket + descriptor.

    macOS AF_UNIX paths cap around 104 chars; pytest's ``tmp_path`` lives
    under ``/var/folders/...`` which is well over that. ``/tmp`` keeps us
    under the limit.
    """
    base = Path(tempfile.mkdtemp(prefix="bm-daemon-", dir="/tmp"))
    try:
        yield base
    finally:
        # Best-effort teardown in case the daemon stop() raised mid-test.
        descriptor_path = base / "daemon.json"
        descriptor = DaemonDescriptor.load(descriptor_path)
        if descriptor is not None and is_alive(descriptor.pid):
            try:
                stop(descriptor_path=descriptor_path, timeout_s=2.0)
            except Exception:
                pass
        shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def daemon_subprocess(short_state_dir):
    """Spawn a real daemon subprocess against the test's tmp paths.

    Yields the ``DaemonDescriptor`` the daemon published. Stops the
    daemon on teardown. Each test owns its socket + descriptor; no
    shared state between tests.
    """
    socket_path = short_state_dir / "daemon.sock"
    descriptor_path = short_state_dir / "daemon.json"
    descriptor = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        yield descriptor
    finally:
        try:
            stop(descriptor_path=descriptor_path)
        except Exception:
            pass


__all__ = ["daemon_subprocess", "short_state_dir"]
