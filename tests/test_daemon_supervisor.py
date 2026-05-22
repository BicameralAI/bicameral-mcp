"""Phase 2a: supervisor start/stop idempotency + descriptor file contract.

Phase 2c will add real OS-level process spawning + LaunchAgent integration.
These tests cover the in-process lifecycle controller that's enough to
host the protocol surface and serve MCP today.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from daemon.registry import AdapterRegistry
from daemon.supervisor import Supervisor, SupervisorStatus
from protocol.client import ProtocolClient


@pytest.fixture
def short_state_dir():
    """macOS AF_UNIX limit; pytest tmp_path is too deep for the socket."""
    base = Path(tempfile.mkdtemp(prefix="bm-", dir="/tmp"))
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _make_supervisor(state_dir: Path) -> Supervisor:
    return Supervisor(
        registry=AdapterRegistry(),
        socket_path=state_dir / "d.sock",
        descriptor_path=state_dir / "daemon.json",
    )


async def test_start_writes_descriptor_with_socket_and_pid(
    short_state_dir: Path,
) -> None:
    """After start(), daemon.json carries the socket path + PID."""
    import os

    sup = _make_supervisor(short_state_dir)
    try:
        await sup.start()
        descriptor = json.loads(
            (short_state_dir / "daemon.json").read_text(encoding="utf-8")
        )
        assert descriptor["socket_path"] == str(short_state_dir / "d.sock")
        assert descriptor["pid"] == os.getpid()
        assert sup.status == SupervisorStatus.RUNNING
    finally:
        await sup.stop()


async def test_double_start_is_idempotent_no_second_runtime(
    short_state_dir: Path,
) -> None:
    """Two start() calls produce one Runtime instance, not two."""
    sup = _make_supervisor(short_state_dir)
    try:
        await sup.start()
        runtime_after_first = sup.runtime
        await sup.start()
        assert sup.runtime is runtime_after_first
        assert sup.status == SupervisorStatus.RUNNING
    finally:
        await sup.stop()


async def test_stop_removes_descriptor_and_transitions_to_stopped(
    short_state_dir: Path,
) -> None:
    """Clean shutdown deletes daemon.json + flips status."""
    sup = _make_supervisor(short_state_dir)
    await sup.start()
    assert (short_state_dir / "daemon.json").exists()
    await sup.stop()
    assert not (short_state_dir / "daemon.json").exists()
    assert sup.status == SupervisorStatus.STOPPED


async def test_protocol_client_connects_via_descriptor_socket_path(
    short_state_dir: Path,
) -> None:
    """End-to-end: client reads daemon.json, connects, handshake passes."""
    sup = _make_supervisor(short_state_dir)
    try:
        await sup.start()
        descriptor = json.loads(
            (short_state_dir / "daemon.json").read_text(encoding="utf-8")
        )
        client = ProtocolClient(socket_path=Path(descriptor["socket_path"]))
        try:
            await client.connect()  # verifies system.version handshake
        finally:
            await client.close()
    finally:
        await sup.stop()


async def test_restart_starts_new_runtime_instance(short_state_dir: Path) -> None:
    """After restart(), the runtime is a fresh instance."""
    sup = _make_supervisor(short_state_dir)
    try:
        await sup.start()
        runtime_before = sup.runtime
        await sup.restart()
        assert sup.runtime is not runtime_before
        assert sup.status == SupervisorStatus.RUNNING
    finally:
        await sup.stop()
