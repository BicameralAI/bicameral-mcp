"""MCP-side EgressAdapter shell.

Phase 2b stub: writes a stderr marker including tenant_id from the
ConnectionContext so dispatch is verifiable in tests. Phase 2c rewires
this to the existing ``notifications/stderr.py`` channel (and any other
channels MCP exposes today) so the daemon's egress router replaces the
in-handler emit shortcut without losing observable behavior.
"""

from __future__ import annotations

import sys

from protocol.contracts import ConnectionContext, DeliveryResult, NotificationEvent


class MCPEgressAdapter:
    """Registers as EgressAdapter named ``mcp``."""

    name = "mcp"

    async def deliver(self, event: NotificationEvent, ctx: ConnectionContext) -> DeliveryResult:
        marker = f"[bicameral.egress tenant={ctx.tenant_id}] {event.event_type}: {event.summary}\n"
        sys.stderr.write(marker)
        sys.stderr.flush()
        return DeliveryResult(status="delivered")
