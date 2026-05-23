"""Entry point for the daemon subprocess (``python -m daemon serve``).

Spawned by ``bicameral-mcp daemon start`` via ``subprocess.Popen``. Runs the
asyncio event loop until SIGTERM / SIGINT, at which point it gracefully
stops the ``Supervisor`` (closes the UDS socket, removes the descriptor,
unregisters adapters).

Stays minimal on purpose — process lifecycle plumbing only. The actual RPC
surface lives in ``protocol/`` + ``daemon/runtime.py``; the adapter wiring
lives in ``integrations/mcp_adapter/bootstrap.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from integrations.mcp_adapter.bootstrap import bootstrap_mcp_daemon


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m daemon")
    sub = parser.add_subparsers(dest="action", required=True)
    serve = sub.add_parser("serve", help="run the daemon process until SIGTERM")
    serve.add_argument(
        "--socket",
        type=Path,
        default=None,
        help="override the default UDS socket path (~/.bicameral/daemon.sock)",
    )
    serve.add_argument(
        "--descriptor",
        type=Path,
        default=None,
        help="override the descriptor path (~/.bicameral/daemon.json)",
    )
    return parser.parse_args(argv)


async def _serve(socket_path: Path | None, descriptor_path: Path | None) -> int:
    supervisor = await bootstrap_mcp_daemon(
        socket_path=socket_path,
        descriptor_path=descriptor_path,
    )
    stop_event = asyncio.Event()

    def _trigger_stop(_signum: int | None = None, _frame: object = None) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _trigger_stop)
        except NotImplementedError:
            # Windows asyncio doesn't support add_signal_handler. Fall
            # back to the synchronous signal module; the handler sets the
            # event from any thread and asyncio picks it up on the next
            # event-loop tick.
            signal.signal(sig, _trigger_stop)

    try:
        await stop_event.wait()
    finally:
        await supervisor.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.action == "serve":
        return asyncio.run(_serve(args.socket, args.descriptor))
    return 1


if __name__ == "__main__":
    sys.exit(main())
