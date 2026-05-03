"""PostToolUse hook for the ``Bash`` tool — git write-op detector.

When the agent runs ``git commit`` / ``git merge`` / ``git pull`` /
``git rebase --continue``, inject a system-reminder telling the agent to
call ``/bicameral:sync`` so the decision ledger picks up the new HEAD,
runs compliance checks, and produces authoritative reflected/drifted
verdicts before the next user turn.

Replaces the plain-stdout one-liner ``_BICAMERAL_POST_COMMIT_COMMAND``
that previously lived inline in ``setup_wizard.py``. Per Claude Code
2.x hook docs (https://code.claude.com/docs/en/hooks), plain stdout
from PostToolUse hooks is silently dropped to the debug log — only
UserPromptSubmit / UserPromptExpansion / SessionStart treat raw stdout
as agent-visible context. Symptom: the agent committed but never
followed through to call ``link_commit`` / ``/bicameral:sync`` because
the reminder never reached the model. Fix: emit the structured
envelope ``{"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}}``.

The reminder text preserves the canonical ``"bicameral: new commit
detected"`` phrase — the ``bicameral-sync`` skill watches for that
exact prefix as one of its trigger signals.

Errors are swallowed silently (exit 0, empty response) so a broken
hook never blocks a user.
"""

from __future__ import annotations

import json
import sys

BASH_TOOL_NAME = "Bash"

# Substrings that mark a git write-op against HEAD that the agent should
# follow up with /bicameral:sync. Exact phrasing matches the legacy
# inline command's tuple so behavior is byte-identical except for the
# stdout envelope.
WRITE_OP_MARKERS: tuple[str, ...] = (
    "git commit",
    "git merge ",
    "git pull",
    "git rebase --continue",
)

REMINDER_TEXT = (
    "bicameral: new commit detected — run /bicameral:sync to resolve "
    "compliance and get authoritative reflected/drifted status"
)


def _is_git_write_op(command: str) -> bool:
    return any(marker in command for marker in WRITE_OP_MARKERS)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != BASH_TOOL_NAME:
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not _is_git_write_op(command):
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": REMINDER_TEXT,
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
