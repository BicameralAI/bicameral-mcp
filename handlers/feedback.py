"""Handler for bicameral.feedback.

Records agent self-reported friction (trying_to / attempted / stuck_on)
as a PostHog telemetry event. No ledger write.

Extracted from server.py in Phase 2c-1 (#daemon-extraction parent plan
§Phase 2c-1).

Phase 2c-6a: split into a thin MCP-side facade (handle_feedback) that
delegates to the daemon when available, and a pure-impl core
(_handle_feedback_impl) that both the facade's fallback path and the
daemon's server-side dispatcher (protocol/handlers/writes.py) call
directly to avoid infinite RPC loops.
"""

from __future__ import annotations

import logging
from typing import Any

from protocol.categorization import write_tool

logger = logging.getLogger(__name__)


async def _handle_feedback_impl(
    *,
    server_version: str,
    skill: str = "",
    trying_to: str = "",
    attempted: str = "",
    stuck_on: str = "",
) -> dict[str, Any]:
    """Core feedback logic — pure PostHog event emit, no daemon call.

    Invoked by the daemon's ``write.feedback`` protocol handler (via
    protocol/handlers/writes.py) and by the MCP-side facade when the
    daemon is not reachable. The decorator lives on the facade only.
    """
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


@write_tool("write.feedback")
async def handle_feedback(
    *,
    server_version: str,
    skill: str = "",
    trying_to: str = "",
    attempted: str = "",
    stuck_on: str = "",
) -> dict[str, Any]:
    """MCP-side facade for ``write.feedback``.

    Phase 2c-6a — if a daemon proxy is available via BicameralContext,
    delegate to it. Otherwise fall through to _handle_feedback_impl so
    test environments and non-daemon installs keep working.
    """
    try:
        from context import BicameralContext

        ctx = BicameralContext.from_env()
        daemon = getattr(ctx, "daemon", None)
    except Exception:
        daemon = None

    if daemon is not None:
        try:
            from protocol.contracts import FeedbackResult

            raw = await daemon.feedback(
                server_version=server_version,
                skill=skill,
                trying_to=trying_to,
                attempted=attempted,
                stuck_on=stuck_on,
            )
            return FeedbackResult.model_validate(raw).model_dump()
        except Exception:
            logger.debug(
                "[handle_feedback] daemon call failed, falling through to in-process impl",
                exc_info=True,
            )

    return await _handle_feedback_impl(
        server_version=server_version,
        skill=skill,
        trying_to=trying_to,
        attempted=attempted,
        stuck_on=stuck_on,
    )
