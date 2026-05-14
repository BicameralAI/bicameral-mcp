#!/usr/bin/env python3
"""Claude Code PreToolUse hook — emit timeout posture before bicameral tool calls.

Fires before a ledger-touching tool runs. Reads the recent-timeout ring
buffer + current budgets and emits a one-line summary to stderr so
the Claude agent can reason about whether to fire the query at all,
choose ``timeout_class="drift"`` thoughtfully, or back off after
observed degradation.

**Always exits 0.** Hook is advisory; the server-side
``asyncio.wait_for`` wrap is the deterministic gate.

The hook reads stdin (Claude Code passes a JSON envelope describing
the about-to-fire tool) but does not parse it deeply — emitting the
posture line is universally useful before any bicameral tool, so we
skip envelope-shape coupling and just always print.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # Drain stdin so Claude Code's pipe doesn't backpressure.
    try:
        sys.stdin.read()
    except Exception:
        pass

    repo = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if repo not in sys.path:
        sys.path.insert(0, repo)

    try:
        from ledger.timeout_telemetry import recent_timeout_counts
    except Exception:
        return 0

    try:
        counts = recent_timeout_counts(window_seconds=600.0)  # 10 min window
    except Exception:
        return 0

    if counts.get("read", 0) == 0 and counts.get("drift", 0) == 0:
        # Quiet path — no posture-changing signal to surface.
        return 0

    sys.stderr.write(
        "[bicameral] recent ledger-query timeouts (last 10 min): "
        f"{counts.get('read', 0)} read / {counts.get('drift', 0)} drift — "
        "consider whether the next query may also be slow\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
