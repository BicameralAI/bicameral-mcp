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

#402: Slash-command prompts (``/qor-plan <issue-url>``) were silently
skipping the gate because the classifier's verb list missed ``plan``
and had no slash-command awareness. The hook now defers to
:func:`classify_prompt` and records a ``trigger_evaluated`` JSONL event
carrying the ``prompt_surface_form`` so regressions in the trigger
surface are visible to the operator.

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
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.preflight_intent import ClassifyResult, classify_prompt  # noqa: E402

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

# #402 — trigger_evaluated telemetry: where the hook records each prompt's
# classification + surface form. Local JSONL, append-only, mode 0o600.
# The PostHog uplink for this stream is deferred — the hook is a subprocess
# that exits in ~ms, so synchronous network I/O is not viable. A follow-up
# will drain this file from the long-lived MCP server.
_TRIGGER_LOG = Path.home() / ".bicameral" / "preflight_trigger_evaluated.jsonl"
_TELEMETRY_OFF = frozenset({"0", "false", "no", "off"})


def _telemetry_disabled() -> bool:
    """Return True iff the operator has globally disabled bicameral telemetry.

    Mirrors the check in ``telemetry.py`` so the trigger log obeys the same
    opt-out switch users already know. Default: enabled.
    """
    raw = os.environ.get("BICAMERAL_TELEMETRY", "").strip().lower()
    return raw in _TELEMETRY_OFF


def _record_trigger_evaluated(result: ClassifyResult) -> None:
    """Append a single ``trigger_evaluated`` row to the local JSONL log.

    Best-effort: any I/O failure is swallowed so a broken telemetry path
    never blocks a user prompt. Privacy: the prompt itself is NOT
    recorded — only the classifier's surface-form label and fire bit,
    plus the slash-command name when present (already public — the user
    just typed it).
    """
    if _telemetry_disabled():
        return
    try:
        _TRIGGER_LOG.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": "preflight.trigger_evaluated",
            "ts": datetime.now(UTC).isoformat(),
            "fired": result.fire,
            "prompt_surface_form": result.prompt_surface_form,
            "slash_command": result.slash_command,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        fd = os.open(str(_TRIGGER_LOG), flags, 0o600)
        try:
            with os.fdopen(fd, "ab") as f:
                f.write(line.encode("utf-8"))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
    except OSError:
        return


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    prompt = payload.get("prompt", "") if isinstance(payload, dict) else ""
    result = classify_prompt(prompt)
    _record_trigger_evaluated(result)
    if result.fire:
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
