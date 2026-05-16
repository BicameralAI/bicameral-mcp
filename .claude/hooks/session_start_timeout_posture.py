#!/usr/bin/env python3
"""Claude Code SessionStart hook — surface ledger-query timeout posture.

Runs once when a Claude Code session starts. Reads the bicameral
ledger-query timeout configuration + the recent-timeout ring buffer
and prints a one-line brief to stderr; Claude Code surfaces stderr
from hooks back to the model as a context fragment.

**Always exits 0.** Never blocks the session. If the bicameral package
isn't importable (e.g. running in a checkout without the venv), prints
a single warning and exits 0. The deterministic server-side timeout
wrap remains the source of truth regardless of whether this hook
runs at all.

Per #224 + the feedback-claude-hooks-for-mcp-context memory:
deterministic gate (asyncio.wait_for in ledger/client.py) is the
floor; this hook is advisory context enrichment for the Claude
agent only.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    repo = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    # Add the repo root to sys.path so we can import the bicameral
    # package without a venv hop. Hooks run in whatever shell Claude
    # Code launched — they have no guarantee of import context.
    if repo not in sys.path:
        sys.path.insert(0, repo)

    try:
        from context import (
            _read_query_timeout_drift_seconds,
            _read_query_timeout_read_seconds,
        )
        from ledger.timeout_telemetry import recent_timeout_counts
    except Exception as exc:
        sys.stderr.write(f"[bicameral hook] timeout-posture unavailable: {exc}\n")
        return 0

    try:
        read_budget = _read_query_timeout_read_seconds(repo)
        drift_budget = _read_query_timeout_drift_seconds(repo)
    except Exception:
        read_budget = 5.0
        drift_budget = 30.0

    try:
        counts = recent_timeout_counts()
    except Exception:
        counts = {"read": 0, "drift": 0}

    env_disabled = os.environ.get("BICAMERAL_QUERY_TIMEOUT_DISABLE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    env_disabled_str = "on" if env_disabled else "off"

    sys.stderr.write(
        "[bicameral] query timeouts last 1h: "
        f"{counts.get('read', 0)} read / {counts.get('drift', 0)} drift "
        f"| budgets: {read_budget:.1f}s / {drift_budget:.1f}s "
        f"| env-disable: {env_disabled_str}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
