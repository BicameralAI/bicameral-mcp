"""PostToolUse hook for ``bicameral.preflight``.

When preflight surfaces ≥1 decision, inject a system-reminder templating
the correction-capture loop (Step 5.6 of ``skills/bicameral-preflight``).
Per #175, the agent does NOT judge contradiction itself — instead it
asks the user via ``AskUserQuestion`` (Step 5.6.1) and acts mechanically
on the answer (Step 5.6.2):

  1. ``AskUserQuestion`` — disambiguate whether the current request is
     a refinement of any surfaced decision. Three options: supersede,
     keep_both, unrelated.
  2. If 'supersede' or 'keep_both':
     - ``bicameral.ingest(source="agent_session", ...)``
     - ``bicameral.resolve_collision(new_id=..., old_id=..., action=...)``
  3. If 'unrelated': skip capture, proceed to implementation.

Why route the judgment to the user (path D in the #175 design discussion):
prior implementations tried (a) a conditional "IF you contradict ..." gate
which let the agent skip on borderline prompts, then (b) an unconditional
"you MUST capture" gate which the agent still ignored on structural-
mismatch prompts (e.g. "add programmatic API" vs "drag-and-drop UX"
decision — agent rationalized "these can coexist" and skipped). The
contradiction judgment is semantic, not lexical, and LLM-level inference
is unreliable on it. The user is the only party with the actual intent;
the skill puts the question to them.

Trust contract preserved: the hook only fires when ``fired=True``
AND ``len(decisions) > 0`` — silent on no signal. The question runs at
a moment the flow is already paused (rendering the surfaced block).

Per Claude Code 2.x hook contract: read JSON ``{tool_name, tool_input,
tool_response}`` from stdin. ``tool_response`` is either a JSON string or
a dict — both are handled. Output is the structured envelope
``{"hookSpecificOutput": {"hookEventName": "PostToolUse",
"additionalContext": "..."}}`` written to stdout; the CLI surfaces
``additionalContext`` next to the tool result the model sees on the next
turn. Plain stdout is silently dropped to the debug log for PostToolUse
events (per https://code.claude.com/docs/en/hooks — only
UserPromptSubmit / UserPromptExpansion / SessionStart treat raw stdout
as agent-visible context). Errors swallowed silently (exit 0, empty
response) so a broken hook never blocks a user.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hooks.preflight_intent import suppress_capture_reminder  # noqa: E402
from hooks.session_prompt_store import read_session_prompt  # noqa: E402

PREFLIGHT_TOOL_NAME = "mcp__bicameral__bicameral_preflight"


def _coerce_response(raw: object) -> dict:
    """Return a dict view of ``tool_response`` whether it arrived as a
    JSON string or already-decoded dict. On any failure return ``{}`` —
    the caller treats an empty dict as "no decisions to template."
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _format_reminder(decisions: list[dict]) -> str:
    bullets = "\n".join(
        f"  - {d.get('decision_id', '<unknown>')}: {d.get('description', '<no description>')}"
        for d in decisions
    )
    first = decisions[0]
    first_id = first.get("decision_id", "<decision_id>")
    first_desc = first.get("description", "<description>")
    return (
        "<system-reminder>\n"
        f"bicameral.preflight surfaced {len(decisions)} prior decision(s):\n"
        f"{bullets}\n"
        "\n"
        "BEFORE any code edits, do NOT judge contradiction yourself — ask "
        "the user. Per skills/bicameral-preflight Step 5.6.1, call "
        "AskUserQuestion to disambiguate whether the current request is a "
        "refinement of any surfaced decision. The user (not the agent) "
        "decides; the agent then acts mechanically on the answer.\n"
        "\n"
        "AskUserQuestion({\n"
        '  "question": "Your request appears to operate on the same feature '
        f"surface as surfaced decision {first_id} "
        f'(\\"{first_desc[:100]}\\"). Treat this work as a refinement of that prior plan?",'
        "\n"
        '  "multiSelect": False,\n'
        '  "options": [\n'
        '    {"label": "Yes — supersede prior plan",\n'
        '     "description": "<paraphrase user\'s direction; replaces the prior wholesale>"},\n'
        '    {"label": "Yes — keep both (addition or scoping)",\n'
        '     "description": "<paraphrase; adds to or narrows; both remain>"},\n'
        '    {"label": "No — unrelated to prior plan",\n'
        '     "description": "Continue without capture"},\n'
        "  ],\n"
        "})\n"
        "\n"
        "Branch on the answer:\n"
        "  - 'supersede'   → bicameral.ingest(source='agent_session') +\n"
        "                    bicameral.resolve_collision(action='supersede')\n"
        "  - 'keep both'   → bicameral.ingest(source='agent_session') +\n"
        "                    bicameral.resolve_collision(action='keep_both')\n"
        "  - 'unrelated'   → skip capture; proceed to implementation; narrate one\n"
        "                    line ('noted — surfaced context isn't applicable here').\n"
        "\n"
        "If multiple decisions were surfaced and the user's request plausibly\n"
        "touches more than one, ask once per plausibly-touched decision; skip\n"
        "the question for surfaced decisions clearly unrelated to the prompt.\n"
        "\n"
        "Asking is cheap; missed capture is irreversible context loss. Default\n"
        "to asking; only skip when the surfaced decision is obviously off-topic.\n"
        "</system-reminder>"
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("tool_name") != PREFLIGHT_TOOL_NAME:
        return 0
    response = _coerce_response(payload.get("tool_response"))
    if not response.get("fired"):
        return 0
    decisions = response.get("decisions") or []
    if not isinstance(decisions, list) or not decisions:
        return 0
    # #170 — suppress on read-only/informational prompts. Recall-biased: a
    # missing/unreadable prompt or any implementation-verb prompt fires, so a
    # refinement (smuggled or not) is never silently dropped (#175 invariant).
    prompt = read_session_prompt(str(payload.get("session_id") or "")) or ""
    if suppress_capture_reminder(prompt):
        return 0
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": _format_reminder(decisions),
            }
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
