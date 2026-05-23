"""Cross-platform process lifecycle helpers for the bicameral daemon.

The spawn model is the same on every platform — ``subprocess.Popen`` with
detach flags appropriate to the OS, child writes ``~/.bicameral/daemon.json``
via the supervisor, parent exits. Stop sends SIGTERM (POSIX) or invokes
``terminate()`` which maps to ``TerminateProcess`` (Windows). No double-fork,
no LaunchAgent in this layer — auto-start is its own follow-up PR.

This module deliberately does NOT supervise the spawned child. A daemon that
crashes stays crashed until the user runs ``bicameral-mcp daemon start``
again. Restart-on-crash is the OS supervisor's job (launchd / systemd /
Windows Service Control Manager), introduced as a separate concern.

Phase 2c-3 — daemon-as-process arc, plan in
``thoughts/shared/plans/2026-05-22-daemon-as-process-arc.md``.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from daemon.supervisor import default_descriptor_path, default_socket_path


class DaemonNotRunningError(RuntimeError):
    """Raised when an operation needs a running daemon but none is."""


class DaemonAlreadyRunningError(RuntimeError):
    """Raised when ``start`` is called and the descriptor points to a live process."""


@dataclass
class DaemonDescriptor:
    """Read-side mirror of ``daemon.supervisor._DaemonDescriptor``."""

    socket_path: Path
    pid: int

    @classmethod
    def load(cls, path: Path) -> DaemonDescriptor | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(socket_path=Path(data["socket_path"]), pid=int(data["pid"]))
        except (json.JSONDecodeError, KeyError, ValueError):
            return None


def is_alive(pid: int) -> bool:
    """Return True iff ``pid`` names a running process this user can signal."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # ``OpenProcess`` is the canonical Windows check, but we can rely on
        # the simpler ``kill(0)`` semantics via the ``signal`` module on 3.11+.
        # Falling back to a process listing avoids loading ctypes for one line.
        try:
            os.kill(pid, 0)
            return True
        except (OSError, PermissionError):
            return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user; we treat as alive for
        # the "is the daemon up?" question. The caller can still try to stop
        # it and will get a clean permission error.
        return True


def spawn(
    *,
    socket_path: Path | None = None,
    descriptor_path: Path | None = None,
    log_path: Path | None = None,
    wait_timeout_s: float = 5.0,
) -> DaemonDescriptor:
    """Spawn a detached daemon subprocess and wait for the descriptor.

    Returns the descriptor the child wrote. Raises
    ``DaemonAlreadyRunningError`` if an existing descriptor points to a live
    PID. Raises ``TimeoutError`` if the child hasn't published its
    descriptor within ``wait_timeout_s``.

    The child runs ``python -m daemon serve`` with detach flags appropriate
    to the host OS. Parent process exits as soon as the descriptor file
    appears + the process is verifiably alive.
    """
    socket_path = socket_path or default_socket_path()
    descriptor_path = descriptor_path or default_descriptor_path()
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)

    existing = DaemonDescriptor.load(descriptor_path)
    if existing is not None and is_alive(existing.pid):
        raise DaemonAlreadyRunningError(
            f"daemon already running (pid={existing.pid}, socket={existing.socket_path})"
        )
    # Stale descriptor (process gone) — remove so the child can rewrite cleanly.
    if existing is not None and not is_alive(existing.pid):
        descriptor_path.unlink(missing_ok=True)

    log_path = log_path or descriptor_path.parent / "daemon.log"
    log_fh = open(log_path, "a", encoding="utf-8")

    argv = [
        sys.executable,
        "-m",
        "daemon",
        "serve",
        "--socket",
        str(socket_path),
        "--descriptor",
        str(descriptor_path),
    ]
    if sys.platform == "win32":
        # DETACHED_PROCESS hides the console window; CREATE_NEW_PROCESS_GROUP
        # lets us send signals (Ctrl+Break) to the child group later. Both
        # are required for a clean Windows detach.
        creationflags = (
            subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
            | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        )
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
            creationflags=creationflags,
        )
    else:
        # ``start_new_session`` runs ``setsid`` in the child so it survives
        # the parent shell exiting.
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )

    # Wait for the child to publish its descriptor — this is the liveness
    # signal that the asyncio loop is up and the UDS socket is bound.
    deadline = time.monotonic() + wait_timeout_s
    while time.monotonic() < deadline:
        # Child died before publishing → propagate as a hard error.
        if proc.poll() is not None:
            log_fh.close()
            raise RuntimeError(
                f"daemon process exited before publishing descriptor "
                f"(exit code {proc.returncode}); see {log_path}"
            )
        descriptor = DaemonDescriptor.load(descriptor_path)
        if descriptor is not None and descriptor.pid == proc.pid:
            log_fh.close()
            return descriptor
        time.sleep(0.05)

    # Timeout — kill the orphan and bail.
    proc.terminate()
    log_fh.close()
    raise TimeoutError(
        f"daemon did not publish descriptor within {wait_timeout_s:.1f}s; see {log_path}"
    )


def stop(
    *,
    descriptor_path: Path | None = None,
    timeout_s: float = 10.0,
) -> None:
    """Send SIGTERM to the running daemon and wait for clean exit.

    Reads the PID from the descriptor. After ``timeout_s`` with the process
    still alive, escalates to SIGKILL / ``TerminateProcess`` and returns.
    Removes the descriptor file last so a partial stop leaves observable
    state.

    Raises ``DaemonNotRunningError`` if no descriptor exists or its PID is
    already dead.
    """
    descriptor_path = descriptor_path or default_descriptor_path()
    descriptor = DaemonDescriptor.load(descriptor_path)
    if descriptor is None or not is_alive(descriptor.pid):
        # Idempotent: a stop on an already-stopped daemon cleans up the
        # stale descriptor and returns. Callers shouldn't have to special-
        # case "stop after a crash."
        descriptor_path.unlink(missing_ok=True)
        if descriptor is None:
            raise DaemonNotRunningError("no daemon descriptor found")
        return

    # Phase 1: polite SIGTERM. The supervisor's signal handler closes the
    # asyncio loop, the supervisor stops, descriptor is removed by the
    # child itself.
    try:
        if sys.platform == "win32":
            # ``CTRL_BREAK_EVENT`` is what corresponds to SIGINT for processes
            # launched with ``CREATE_NEW_PROCESS_GROUP``.
            os.kill(descriptor.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            os.kill(descriptor.pid, signal.SIGTERM)
    except ProcessLookupError:
        descriptor_path.unlink(missing_ok=True)
        return

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not is_alive(descriptor.pid):
            descriptor_path.unlink(missing_ok=True)
            return
        time.sleep(0.1)

    # Phase 2: SIGKILL escalation. The supervisor didn't shut down cleanly;
    # force it. Descriptor is removed regardless so the next ``start`` doesn't
    # see a phantom-alive entry.
    try:
        if sys.platform == "win32":
            # Last-resort kill on Windows.
            subprocess.run(
                ["taskkill", "/PID", str(descriptor.pid), "/F"],
                check=False,
                capture_output=True,
            )
        else:
            os.kill(descriptor.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    descriptor_path.unlink(missing_ok=True)


def status(*, descriptor_path: Path | None = None) -> dict[str, object]:
    """Return a JSON-serializable status snapshot for ``daemon status`` output.

    Shape:
        {"status": "stopped" | "running" | "stale",
         "pid": int | None,
         "socket_path": str | None,
         "descriptor_path": str}

    ``"stale"`` means a descriptor exists but the PID isn't alive — usually
    a crash without cleanup. Operators should run ``daemon stop`` (idempotent
    cleanup) before ``daemon start``.
    """
    descriptor_path = descriptor_path or default_descriptor_path()
    descriptor = DaemonDescriptor.load(descriptor_path)
    if descriptor is None:
        return {
            "status": "stopped",
            "pid": None,
            "socket_path": None,
            "descriptor_path": str(descriptor_path),
        }
    if not is_alive(descriptor.pid):
        return {
            "status": "stale",
            "pid": descriptor.pid,
            "socket_path": str(descriptor.socket_path),
            "descriptor_path": str(descriptor_path),
        }
    return {
        "status": "running",
        "pid": descriptor.pid,
        "socket_path": str(descriptor.socket_path),
        "descriptor_path": str(descriptor_path),
    }
