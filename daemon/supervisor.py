"""Supervisor — in-process lifecycle controller for the daemon Runtime.

Writes ``~/.bicameral/daemon.json`` so ProtocolClients can discover the
socket without coordinating out-of-band. ``start()`` is idempotent — a
second call returns the same status.

Phase 2c wires this up to a real OS-level process (LaunchAgent on macOS,
systemd-user on Linux, TBD on Windows) so the daemon survives shell
sessions. Today the supervisor only manages the runtime *within the
calling process* — which is enough to test the protocol contract end-to-
end and is also exactly what MCP needs when no system daemon is yet
installed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .registry import AdapterRegistry
from .runtime import Runtime


class SupervisorError(Exception):
    """Raised on lifecycle violations (e.g., stop before start)."""


class SupervisorStatus(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"


@dataclass
class _DaemonDescriptor:
    """Wire format for ``~/.bicameral/daemon.json``."""

    socket_path: str
    pid: int

    def to_json(self) -> str:
        return json.dumps({"socket_path": self.socket_path, "pid": self.pid})


def default_state_dir() -> Path:
    return Path.home() / ".bicameral"


def default_socket_path() -> Path:
    return default_state_dir() / "daemon.sock"


def default_descriptor_path() -> Path:
    return default_state_dir() / "daemon.json"


class Supervisor:
    def __init__(
        self,
        registry: AdapterRegistry | None = None,
        socket_path: Path | None = None,
        descriptor_path: Path | None = None,
    ) -> None:
        self._registry = registry or AdapterRegistry()
        self._socket_path = socket_path or default_socket_path()
        self._descriptor_path = descriptor_path or default_descriptor_path()
        self._runtime: Runtime | None = None
        self._status: SupervisorStatus = SupervisorStatus.STOPPED

    @property
    def registry(self) -> AdapterRegistry:
        return self._registry

    @property
    def runtime(self) -> Runtime | None:
        return self._runtime

    @property
    def status(self) -> SupervisorStatus:
        return self._status

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    async def start(self) -> SupervisorStatus:
        if self._status == SupervisorStatus.RUNNING:
            return self._status
        runtime = Runtime(self._socket_path, self._registry)
        await runtime.start()
        self._runtime = runtime
        self._write_descriptor()
        self._status = SupervisorStatus.RUNNING
        return self._status

    async def stop(self) -> SupervisorStatus:
        if self._status == SupervisorStatus.STOPPED:
            return self._status
        assert self._runtime is not None  # invariant: RUNNING ⇒ runtime exists
        await self._runtime.stop()
        self._runtime = None
        self._remove_descriptor()
        self._status = SupervisorStatus.STOPPED
        return self._status

    async def restart(self) -> SupervisorStatus:
        if self._status == SupervisorStatus.RUNNING:
            await self.stop()
        return await self.start()

    def _write_descriptor(self) -> None:
        import os

        self._descriptor_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = _DaemonDescriptor(
            socket_path=str(self._socket_path),
            pid=os.getpid(),
        )
        self._descriptor_path.write_text(descriptor.to_json(), encoding="utf-8")

    def _remove_descriptor(self) -> None:
        if self._descriptor_path.exists():
            self._descriptor_path.unlink()
