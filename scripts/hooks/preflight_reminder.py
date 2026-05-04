"""UserPromptSubmit hook for Claude Code.

When the user prompt indicates code-implementation intent, inject a
system-reminder elevating bicameral.preflight above the agent's default
tool-selection priority — but only as a write-op gate, not a discovery
gate.

Reconciles with #146: that issue's failure mode was the agent doing
file inspection and then NEVER calling preflight at all. The original
fix (#155) over-corrected by telling the agent to call preflight
"before any file-inspection tool". That short-circuited the caller-LLM
discovery the rest of the contract depends on — preflight needs
``file_paths`` populated to do region-anchored retrieval, and the agent
can't extract file paths if we forbid it from looking at the codebase
first.

Updated contract:
  - Read / Grep / Glob FIRST — caller LLM resolves "the reorder feature"
    to concrete file paths.
  - bicameral.preflight(topic, file_paths) — fed by step 1's discovery,
    so the server gets the deterministic binds_to lookup it needs.
  - Write ops (Edit / Write / NotebookEdit / mutating Bash) — preflight
    must precede the first one. This is the line that #146 was
    actually defending; the asserter (assert_flow_2) gates exactly this.

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
    "This prompt indicates code-implementation intent. Auto-fire is the "
    "contract: `bicameral.preflight` MUST run BEFORE any write op (Edit, "
    "Write, NotebookEdit, or Bash that mutates state — git commit, file "
    "writes, mv/rm/etc.).\n"
    "\n"
    "Read-only discovery FIRST, then preflight, then writes.\n"
    "\n"
    "Recommended sequence:\n"
    "  1. Use Read / Grep / Glob to map the user's request to concrete "
    "file paths. The user often names a feature ('the reorder feature') "
    "rather than a file (`reorder.ts`); resolve that mapping yourself "
    "before calling preflight.\n"
    "  2. Call `bicameral.preflight(topic, file_paths)` with BOTH a "
    "natural-language topic AND the concrete file paths discovered in "
    "step 1. `file_paths=[]` defeats region-anchored retrieval — the "
    "server uses these to look up bound decisions deterministically; "
    "topic alone falls back to fuzzy text similarity.\n"
    "  3. Read the surfaced decisions / drifted regions / open questions, "
    "then proceed with the implementation.\n"
    "\n"
    "The skill produces zero output when nothing relevant is found, so "
    "the cost of firing is bounded. Skipping preflight is the contract "
    "violation, not running discovery first.\n"
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
