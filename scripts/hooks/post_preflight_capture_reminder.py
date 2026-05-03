"""PostToolUse hook for ``bicameral.preflight``.

When preflight surfaces ≥1 decision, inject a system-reminder templating
the correction-capture loop (Step 5.6 of ``skills/bicameral-preflight``):

  1. ``bicameral.ingest(source="agent_session", ...)`` — capture the user's
     refinement.
  2. ``bicameral.resolve_collision(new_id=..., old_id=..., action=...)`` —
     wire the refinement to the contradicted decision.

The reminder is *conditional* — it tells the agent "IF your prompt
contradicts a surfaced decision, do this." Preflight has no view of the
user's prompt, so the hook over-fires on any non-empty surfaced block;
the LLM is the gate. Mirrors the existing UserPromptSubmit reminder
pattern in ``preflight_reminder.py`` (over-fires on any
implementation-verb prompt; the skill is the gate).

Per Claude Code 2.x hook contract: read JSON ``{tool_name, tool_input,
tool_response}`` from stdin. ``tool_response`` is either a JSON string or
a dict — both are handled. Output is plain stdout text (the same shape
the existing PostToolUse/Bash hook in ``setup_wizard.py`` uses); the CLI
appends it to the tool result the agent sees on the next turn. Errors
swallowed silently (exit 0, empty response) so a broken hook never
blocks a user.
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
        f"bicameral.preflight surfaced {len(decisions)} decision(s):\n"
        f"{bullets}\n"
        "\n"
        "If your current prompt CONTRADICTS any surfaced decision (signals: "
        '"instead of", "actually we\'re switching to", "no more X", '
        '"I know X said Y but..."), you MUST do this BEFORE any code edits, '
        "per skills/bicameral-preflight Step 5.6:\n"
        "\n"
        "  1. bicameral.ingest(payload={\n"
        '       "query": "<contradicted decision\'s topic>",\n'
        '       "source": "agent_session",\n'
        '       "title": "<short label>",\n'
        '       "date": "<today ISO date>",\n'
        '       "decisions": [{"description": "<user\'s new direction>"}]\n'
        '     }, feature_group="<same feature group as the contradicted decision>")\n'
        "\n"
        "  2. bicameral.resolve_collision(\n"
        '       new_id="<just-ingested refinement id>",\n'
        '       old_id="<contradicted decision_id from list above>",\n'
        '       action="supersede" | "keep_both" | "link_parent"\n'
        "     )\n"
        "\n"
        "Pick action mechanically: supersede (full replace), keep_both "
        "(added constraint), link_parent (child decision under L1 parent). "
        "Do not ask the user — they already stated the refinement.\n"
        "\n"
        "If your prompt is COMPATIBLE with the surfaced decisions, ignore "
        "this and proceed normally.\n"
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
    sys.stdout.write(_format_reminder(decisions))
    return 0


if __name__ == "__main__":
    sys.exit(main())
