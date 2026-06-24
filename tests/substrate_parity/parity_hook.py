#!/usr/bin/env python3
"""Substrate parity probe — a no-op Claude Code hook that records which event
fired, in which substrate, and when.

Prereq for #610/#148 (GH #611 Finding 2): "which hook events actually fire under
`claude -p` and cron/cloud agents" is currently *unverified*. This hook is the
cheap, decisive instrument that converts that assumption into a fact. Register it
on every hook event (see ``settings.fixture.json``), run the same workload under
each substrate, then diff the logs with ``diff_parity.py``.

Contract:
- Reads the Claude Code hook payload as JSON on stdin (``hook_event_name``,
  ``session_id``, ``cwd``, ... per the hooks spec). Tolerates non-JSON / empty
  stdin so it can also be driven from a git hook or a bare cron command.
- Appends ONE JSON line ``{event, substrate, ts, session_id, cwd, pid}`` to the
  log at ``$BICAMERAL_PARITY_LOG``.
- Is a strict no-op: never blocks, never emits a decision, always exits 0. A
  capture probe that changes behavior would poison the very measurement.

Env:
- ``BICAMERAL_PARITY_LOG``       path to the JSONL log (default: ./parity-<substrate>.jsonl)
- ``BICAMERAL_PARITY_SUBSTRATE`` substrate label: ``interactive`` | ``headless`` | ``cron`` (default: ``unknown``)

The event name is taken from the stdin payload's ``hook_event_name``; ``--event``
overrides it for substrates that cannot supply stdin JSON.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys


def _read_payload() -> dict:
    """Best-effort parse of the hook payload from stdin. Never raises."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--event", default=None, help="override hook_event_name")
    args, _ignored = ap.parse_known_args()

    payload = _read_payload()
    substrate = os.environ.get("BICAMERAL_PARITY_SUBSTRATE", "unknown")
    log_path = os.environ.get("BICAMERAL_PARITY_LOG", f"parity-{substrate}.jsonl")

    record = {
        "event": args.event or payload.get("hook_event_name") or "unknown",
        "substrate": substrate,
        "ts": _dt.datetime.now(_dt.UTC).isoformat(),
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd") or os.getcwd(),
        "pid": os.getpid(),
    }

    # Append-only; create parent dirs. Swallow IO errors — the probe must never
    # break the session it is observing.
    try:
        parent = os.path.dirname(os.path.abspath(log_path))
        os.makedirs(parent, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
