"""PostToolUse hook for ``bicameral.preflight``.

When preflight surfaces ≥1 decision, inject a system-reminder templating
the correction-capture loop (Step 5.6 of ``skills/bicameral-preflight``):

  1. ``bicameral.ingest(source="agent_session", ...)`` — capture the user's
     refinement.
  2. ``bicameral.resolve_collision(new_id=..., old_id=..., action=...)`` —
     wire the refinement to the surfaced decision.

The reminder is *unconditional* — Step 5.6 capture is mechanical, not a
judgment call. An earlier conditional shape ("IF your prompt contradicts
a surfaced decision …") let the agent skip capture on borderline prompts
(observed: agent acknowledged the surfaced decision in its text, then
chose 'proceeding with the refactor' without calling ingest /
resolve_collision). The PM resolves ambiguity in the inbox, not the
agent — missed capture is irreversible context loss while a noisy
ledger is reversible.

Tradeoff: the hook now fires on every preflight that returns ≥1
decision, so it spams a refinement-capture reminder even when the
user's request is fully compatible with prior decisions. Tracked as a
follow-up to make this less spammy (e.g. inspect the user prompt via a
co-located UserPromptSubmit handoff, or compare topic similarity to
surfaced decisions).

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
    return (
        "<system-reminder>\n"
        f"bicameral.preflight surfaced {len(decisions)} prior decision(s):\n"
        f"{bullets}\n"
        "\n"
        "BEFORE any code edits, you MUST capture the user's current request "
        "as a refinement of the surfaced decision(s) by calling these two "
        "tools mechanically — do NOT judge whether the request 'really' "
        "contradicts what was surfaced; the PM resolves ambiguity in the "
        "inbox, not the agent. Per skills/bicameral-preflight Step 5.6:\n"
        "\n"
        "  1. bicameral.ingest(payload={\n"
        '       "query": "<surfaced decision\'s topic>",\n'
        '       "source": "agent_session",\n'
        '       "title": "<short label of user\'s direction>",\n'
        '       "date": "<today ISO date>",\n'
        '       "decisions": [{"description": "<user\'s direction, stated as a decision>"}]\n'
        '     }, feature_group="<same feature group as the surfaced decision>")\n'
        "\n"
        "  2. bicameral.resolve_collision(\n"
        '       new_id="<just-ingested refinement id>",\n'
        '       old_id="<surfaced decision_id from list above>",\n'
        '       action="supersede" | "keep_both" | "link_parent"\n'
        "     )\n"
        "\n"
        "Pick action mechanically based on what the user said, not whether "
        "you think it conflicts:\n"
        "  - supersede   — user fully replaces the prior approach\n"
        "                  (e.g. 'X is out, Y instead', 'switching from X to Y')\n"
        "  - keep_both   — user adds, narrows, or scopes alongside the prior\n"
        "                  (e.g. 'also need X for Y', 'only on this surface')\n"
        "  - link_parent — user's direction is a child decision under a higher-\n"
        "                  level surfaced parent (L1 parent → L2 child)\n"
        "\n"
        "Capture is cheap; missed capture is irreversible context loss. Even "
        "if the user's request looks compatible, log the refinement so the "
        "PM has the trace.\n"
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
