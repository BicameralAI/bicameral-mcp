"""CLI surface for MCP-distributed host pre-work adapters.

Wired into the ``bicameral-mcp`` console entrypoint:

    bicameral-mcp adapters status  [--host claude|codex] [--json]
    bicameral-mcp adapters install --host claude|codex --consent
    bicameral-mcp adapters update  --host claude|codex
    bicameral-mcp adapters disable --host claude|codex
    bicameral-mcp adapters uninstall --host claude|codex
    bicameral-mcp prework-run --host claude|codex   # reads host event JSON on stdin

``prework-run`` is what an installed host hook invokes; it is not typically run
by hand.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .registry import get_adapter, supported_hosts
from .runner import run_prework


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bicameral-mcp adapters",
        description="Manage MCP-distributed host pre-work adapters.",
    )
    sub = parser.add_subparsers(dest="adapter_command", required=True)

    for name in ("status", "install", "update", "disable", "uninstall"):
        p = sub.add_parser(name)
        p.add_argument(
            "--host",
            choices=list(supported_hosts()),
            required=(name != "status"),
            help="Target host (required for all actions except status).",
        )
        p.add_argument("--home", help="Override the host config home (for testing).")
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
        if name == "install":
            p.add_argument(
                "--consent",
                action="store_true",
                help="Explicit operator consent to enable the adapter.",
            )
    return parser


def run_adapters_cli(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    home = Path(args.home) if getattr(args, "home", None) else None
    command = args.adapter_command

    if command == "status":
        hosts = [args.host] if args.host else list(supported_hosts())
        payloads = [get_adapter(h, home=home).status().to_dict() for h in hosts]
        if args.json:
            print(json.dumps(payloads, indent=2, sort_keys=True))
        else:
            for payload in payloads:
                _print_status(payload)
        return 0

    adapter = get_adapter(args.host, home=home)
    if command == "install":
        result = adapter.install(consent=bool(getattr(args, "consent", False)))
    elif command == "update":
        result = adapter.update()
    elif command == "disable":
        result = adapter.disable()
    elif command == "uninstall":
        result = adapter.uninstall()
    else:  # pragma: no cover - argparse guarantees a valid command
        parser.error(f"unknown command {command!r}")

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"[{result.host}] {result.action}: {result.message}")
    return 0 if result.ok else 1


def run_prework_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="bicameral-mcp prework-run",
        description="Invoked by a host hook at a session pre-work boundary.",
    )
    parser.add_argument("--host", choices=list(supported_hosts()), required=True)
    parser.add_argument("--home", help="Override the host config home (for testing).")
    args = parser.parse_args(argv)
    home = Path(args.home) if args.home else None

    payload = _read_event_payload()
    result = asyncio.run(run_prework(args.host, payload, home=home))

    # The message is always visible so the operator sees whether preflight ran.
    # Fallbacks are written to stderr and never claim preflight succeeded.
    stream = sys.stderr if (result.is_fallback or not result.preflight_invoked) else sys.stdout
    print(f"[bicameral pre-work / {result.host}] {result.message}", file=stream)
    # Exit 0 so a failed/absent daemon never blocks the host session.
    return 0


def _read_event_payload() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _print_status(payload: dict[str, Any]) -> None:
    print(f"host: {payload['host']}")
    print(f"  state: {payload['state']}")
    print(f"  mechanism: {payload['mechanism']}")
    print(f"  config_path: {payload['config_path']}")
    print(f"  hook_present: {payload['hook_present']}")
    print(f"  capability_supported: {payload['capability_supported']}")
    print(f"  consent_granted: {payload['consent_granted']}")
    print(f"  detail: {payload['detail']}")
