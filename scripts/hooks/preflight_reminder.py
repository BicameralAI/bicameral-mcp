"""UserPromptSubmit hook for Claude Code.

When the user prompt indicates code-implementation intent, inject a
system-reminder elevating bicameral.preflight above the agent's default
tool-selection priority.

Per Claude Code 2.x hook contract: read JSON from stdin, write JSON to
stdout shaped as ``{"hookSpecificOutput": {"hookEventName":
"UserPromptSubmit", "additionalContext": "..."}}``. The legacy top-level
``{"additionalContext": ...}`` shape is silently ignored by the CLI —
the hook still runs and exits 0, but the context never reaches the
model. Errors are swallowed silently (exit 0, empty response) so a
broken hook never blocks a user.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.preflight_intent import should_fire_preflight  # noqa: E402

REMINDER_TEXT = (
    "<system-reminder>\n"
    "This prompt indicates code-implementation intent. Before invoking "
    "any file-inspection tool (Read, Grep, Bash, Glob), call "
    "`bicameral.preflight` to surface prior decisions, drifted regions, "
    "and open questions for the affected area. The skill produces zero "
    "output when nothing relevant is found, so the cost of firing is "
    "bounded. Auto-fire is the contract; do not skip.\n"
    "</system-reminder>"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = payload.get("prompt", "") if isinstance(payload, dict) else ""
    if should_fire_preflight(prompt):
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": REMINDER_TEXT,
                }
            },
            sys.stdout,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
