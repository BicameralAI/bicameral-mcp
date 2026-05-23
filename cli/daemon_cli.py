"""``bicameral-mcp daemon`` CLI — start / stop / restart / status.

Thin shell around ``daemon.process``: argparse plumbing + JSON output for
``status``. Lives under ``cli/`` to match the convention every other
subcommand uses (``sync_and_brief_cli``, ``brief_cli``, etc.).

Phase 2c-3 — daemon-as-process arc. Auto-start install (LaunchAgent /
Windows Service / systemd-user) ships as separate per-platform PRs.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from daemon.process import (
    DaemonAlreadyRunningError,
    DaemonNotRunningError,
    spawn,
    status,
    stop,
)


def _build_argparser(parser: argparse.ArgumentParser) -> None:
    """Add the daemon sub-subcommands (start/stop/restart/status) to ``parser``.

    Called from ``server.py::_register_subparsers`` against the ``daemon``
    subparser slot. Matches the pattern every other CLI module uses.
    """
    sub = parser.add_subparsers(dest="daemon_action", required=False)

    for action_name, help_text in (
        ("start", "spawn the daemon as a detached subprocess (idempotent)"),
        ("stop", "send SIGTERM to the running daemon; SIGKILL after timeout"),
        ("restart", "stop (if running) + start; preserves socket path"),
        ("status", "print daemon liveness as JSON: running | stopped | stale"),
    ):
        sp = sub.add_parser(action_name, help=help_text)
        sp.add_argument(
            "--socket",
            type=Path,
            default=None,
            help="override the UDS socket path (default: ~/.bicameral/daemon.sock)",
        )
        sp.add_argument(
            "--descriptor",
            type=Path,
            default=None,
            help="override the descriptor path (default: ~/.bicameral/daemon.json)",
        )


def main(args: argparse.Namespace) -> int:
    action = getattr(args, "daemon_action", None)
    if action is None:
        # ``bicameral-mcp daemon`` with no subcommand prints status. Useful
        # as a quick-check at the shell.
        print(json.dumps(status(descriptor_path=args.descriptor), indent=2))
        return 0
    if action == "start":
        return _cmd_start(args)
    if action == "stop":
        return _cmd_stop(args)
    if action == "restart":
        return _cmd_restart(args)
    if action == "status":
        print(json.dumps(status(descriptor_path=args.descriptor), indent=2))
        return 0
    print(f"unknown daemon action: {action}", file=sys.stderr)
    return 2


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        descriptor = spawn(
            socket_path=args.socket,
            descriptor_path=args.descriptor,
        )
    except DaemonAlreadyRunningError as exc:
        # Idempotent at the CLI level: report and exit 0. Treating "already
        # running" as an error would force every caller to swallow it.
        print(f"daemon already running: {exc}", file=sys.stderr)
        return 0
    except (TimeoutError, RuntimeError) as exc:
        print(f"daemon start failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "started",
                "pid": descriptor.pid,
                "socket_path": str(descriptor.socket_path),
            },
            indent=2,
        )
    )
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    try:
        stop(descriptor_path=args.descriptor)
    except DaemonNotRunningError as exc:
        # Idempotent: stopping a stopped daemon is a no-op success.
        print(f"daemon already stopped: {exc}", file=sys.stderr)
        return 0
    print(json.dumps({"status": "stopped"}, indent=2))
    return 0


def _cmd_restart(args: argparse.Namespace) -> int:
    # Stop is idempotent — failure to find a running daemon is fine.
    try:
        stop(descriptor_path=args.descriptor)
    except DaemonNotRunningError:
        pass
    return _cmd_start(args)
