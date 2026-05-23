"""Handler for bicameral.dashboard.

Starts the local dashboard HTTP server (idempotent) and returns the URL.
This is a daemon lifecycle operation, not a ledger op — categorized under
``system.*`` in the universal protocol.

Extracted from server.py in Phase 2c-1 (#daemon-extraction parent plan
§Phase 2c-1).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from contracts import DashboardResponse
from dashboard.server import get_dashboard_server
from protocol.categorization import system_tool

if TYPE_CHECKING:
    from context import BicameralContext


@system_tool("system.dashboard")
async def handle_dashboard(ctx_factory: Callable[[], BicameralContext]) -> DashboardResponse:
    srv = get_dashboard_server()
    if not srv.running:
        await srv.start(ctx_factory=ctx_factory)
        status = "started"
    else:
        status = "already_running"
    return DashboardResponse(
        url=srv.url,
        status=status,
        port=srv.port,
    )
