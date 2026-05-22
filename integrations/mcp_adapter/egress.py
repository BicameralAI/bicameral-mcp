"""MCP-side EgressAdapter shell.

Phase 2b stub. Phase 2c rewires this to the existing
``notifications/stderr.py`` channel (and any other channels MCP exposes
today) so the daemon's egress router replaces the in-handler emit
shortcut without losing observable behavior.
"""

from __future__ import annotations

import sys

from protocol.contracts import DeliveryResult, NotificationEvent


class MCPEgressAdapter:
    """Registers as EgressAdapter named ``mcp``.

    Phase 2b writes a one-line marker to stderr so the dispatch path
    leaves a verifiable trace; Phase 2c routes this through the
    structured notification machinery in ``notifications/``.
    """

    name = "mcp"

    async def deliver(self, event: NotificationEvent) -> DeliveryResult:
        marker = f"[bicameral.egress] {event.event_type}: {event.summary}\n"
        sys.stderr.write(marker)
        sys.stderr.flush()
        return DeliveryResult(status="delivered")
