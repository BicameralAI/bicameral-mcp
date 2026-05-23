"""Phase 2c-3: sociable lifecycle tests for the daemon subprocess.

Each test spins up a real ``python -m daemon serve`` subprocess against a
``tmp_path`` socket + descriptor pair. Verifies behavior across the process
boundary — that's the whole point of the daemon-as-process commitment. No
mocks; the only "seam" is the per-test temp dir.

When this batch of tests starts costing measurable CI time, swap the
fixture body for a pooled-warmed-daemon implementation (per Fowler's
ObjectPool guidance) — the tests don't change.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from daemon.process import (
    DaemonAlreadyRunningError,
    DaemonDescriptor,
    DaemonNotRunningError,
    is_alive,
    spawn,
    status,
    stop,
)
from protocol.client import ProtocolClient


@pytest.fixture
def short_state_dir():
    """macOS AF_UNIX paths cap around 104 chars; pytest tmp_path is too deep.

    The daemon writes its descriptor + socket under this dir; tests pass
    explicit paths so no developer ``~/.bicameral/`` state is touched.
    """
    base = Path(tempfile.mkdtemp(prefix="bm-daemon-", dir="/tmp"))
    try:
        yield base
    finally:
        # Best-effort: kill any leftover daemon from a failed assertion.
        descriptor_path = base / "daemon.json"
        descriptor = DaemonDescriptor.load(descriptor_path)
        if descriptor is not None and is_alive(descriptor.pid):
            try:
                stop(descriptor_path=descriptor_path, timeout_s=2.0)
            except Exception:
                pass
        shutil.rmtree(base, ignore_errors=True)


def _paths(state_dir: Path) -> tuple[Path, Path]:
    return state_dir / "daemon.sock", state_dir / "daemon.json"


def test_spawn_publishes_descriptor_and_socket(short_state_dir):
    """The spawned subprocess writes a descriptor + binds the UDS socket."""
    socket_path, descriptor_path = _paths(short_state_dir)
    descriptor = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        assert descriptor.pid > 0
        assert descriptor.socket_path == socket_path
        assert descriptor_path.exists()
        assert socket_path.exists()
        assert is_alive(descriptor.pid)
    finally:
        stop(descriptor_path=descriptor_path)


def test_status_reports_running_then_stopped(short_state_dir):
    """``status`` reflects the actual descriptor + liveness state."""
    socket_path, descriptor_path = _paths(short_state_dir)

    pre = status(descriptor_path=descriptor_path)
    assert pre["status"] == "stopped"
    assert pre["pid"] is None

    descriptor = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        running = status(descriptor_path=descriptor_path)
        assert running["status"] == "running"
        assert running["pid"] == descriptor.pid
        assert running["socket_path"] == str(socket_path)
    finally:
        stop(descriptor_path=descriptor_path)

    post = status(descriptor_path=descriptor_path)
    assert post["status"] == "stopped"
    assert post["pid"] is None


async def test_protocol_client_can_attach_to_spawned_daemon(short_state_dir):
    """Real ProtocolClient → real socket → real daemon → real system.version.

    This is the load-bearing wire test: the JSON-RPC layer round-trips
    across a true subprocess boundary, not a same-process loopback.
    """
    socket_path, descriptor_path = _paths(short_state_dir)
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        client = ProtocolClient(socket_path=socket_path)
        await client.connect()  # version handshake + attach happens inside
        try:
            version = await client._call("system.version", {})
            assert isinstance(version, str)
            assert version  # non-empty
        finally:
            await client.close()
    finally:
        stop(descriptor_path=descriptor_path)


def test_spawn_refuses_when_already_running(short_state_dir):
    """Second ``spawn`` against the same descriptor raises ``DaemonAlreadyRunningError``."""
    socket_path, descriptor_path = _paths(short_state_dir)
    descriptor = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        with pytest.raises(DaemonAlreadyRunningError):
            spawn(socket_path=socket_path, descriptor_path=descriptor_path)
        assert is_alive(descriptor.pid)
    finally:
        stop(descriptor_path=descriptor_path)


def test_stop_raises_when_no_daemon_running(short_state_dir):
    """Stop on a fresh state dir → ``DaemonNotRunningError`` (CLI swallows it)."""
    _, descriptor_path = _paths(short_state_dir)
    with pytest.raises(DaemonNotRunningError):
        stop(descriptor_path=descriptor_path)


def test_stop_cleans_up_stale_descriptor(short_state_dir):
    """A descriptor pointing at a dead PID is silently cleaned up by ``stop``.

    Simulates "daemon crashed without removing its file" — the next start
    must not see a phantom-alive entry. Contract: ``stop`` returns
    successfully (no raise) and removes the stale descriptor.
    """
    _, descriptor_path = _paths(short_state_dir)
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    # PID 999_999_999 is well beyond any plausible live PID — exercises the
    # "descriptor exists but PID is dead" branch in ``stop``.
    descriptor_path.write_text(
        '{"socket_path": "/tmp/bm-stale-not-real.sock", "pid": 999999999}',
        encoding="utf-8",
    )
    # No raise — silent cleanup is the contract for stale descriptors.
    stop(descriptor_path=descriptor_path)
    assert not descriptor_path.exists()


def test_status_reports_stale_descriptor(short_state_dir):
    """When PID is dead but descriptor lives, ``status`` returns ``"stale"``."""
    _, descriptor_path = _paths(short_state_dir)
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(
        '{"socket_path": "/tmp/bm-stale-not-real.sock", "pid": 999999999}',
        encoding="utf-8",
    )
    snapshot = status(descriptor_path=descriptor_path)
    assert snapshot["status"] == "stale"
    assert snapshot["pid"] == 999999999


def test_spawn_after_stop_with_same_paths(short_state_dir):
    """Restart loop: spawn → stop → spawn against identical paths works.

    Observable contract: after stop, a fresh spawn against the same paths
    succeeds and produces a working daemon. We deliberately do NOT assert
    ``is_alive(first.pid)`` is False because POSIX PIDs are reusable —
    after the kernel reaps the daemon, that PID can be assigned to an
    unrelated process within microseconds. What we care about is "can we
    start a new one", which the second ``spawn`` answers.
    """
    socket_path, descriptor_path = _paths(short_state_dir)
    first = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    stop(descriptor_path=descriptor_path)
    assert not descriptor_path.exists()

    second = spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    try:
        assert second.socket_path == socket_path
        assert is_alive(second.pid)
        # Different process: at minimum the descriptor was rewritten by a
        # genuinely-new asyncio loop. Strict ``second.pid != first.pid``
        # is the usual case but not guaranteed by POSIX, so don't assert it.
        _ = first  # silence unused — kept for readability of the restart story
    finally:
        stop(descriptor_path=descriptor_path)


def test_stop_after_stop_is_idempotent_at_cli_layer(short_state_dir):
    """``DaemonNotRunningError`` is the contract; CLI's ``_cmd_stop`` swallows.

    This test pins the contract at the ``daemon.process.stop`` layer; the
    CLI's idempotency is exercised separately if we ever wire a CLI test.
    """
    socket_path, descriptor_path = _paths(short_state_dir)
    spawn(socket_path=socket_path, descriptor_path=descriptor_path)
    stop(descriptor_path=descriptor_path)
    with pytest.raises(DaemonNotRunningError):
        stop(descriptor_path=descriptor_path)
