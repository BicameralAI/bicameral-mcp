"""Handler for bicameral.feedback.

Records agent self-reported friction (trying_to / attempted / stuck_on)
as a PostHog telemetry event. No ledger write.

Extracted from server.py in Phase 2c-1 (#daemon-extraction parent plan
§Phase 2c-1).
"""

from __future__ import annotations

from typing import Any

from protocol.categorization import write_tool


@write_tool("write.feedback")
async def handle_feedback(
    *,
    server_version: str,
    skill: str = "",
    trying_to: str = "",
    attempted: str = "",
    stuck_on: str = "",
) -> dict[str, Any]:
    from telemetry import send_event

    send_event(
        server_version,
        event_type="agent_feedback",
        skill=skill,
        trying_to=trying_to,
        attempted=attempted,
        stuck_on=stuck_on,
    )
    return {"recorded": True}
