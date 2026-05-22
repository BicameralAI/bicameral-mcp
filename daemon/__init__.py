"""bicameral-daemon — user-scoped, repo-separated process owning the ledger,
grounding, dashboard, sync, and audit/governance state.

Phase 2a (this commit): scaffolding only. The daemon supervises its own
lifecycle and hosts the ProtocolServer surface from Phase 1; registered
adapters provide ingest/egress behavior. Code moves into ``daemon/*`` —
ledger, dashboard, grounding, sync — land in Phase 2c. The supervisor
gains real surreal-child spawning + LaunchAgent install in Phase 2c.

Phase 3 extracts this package to the private ``bicameral-daemon`` repo.
"""

from __future__ import annotations

from .registry import AdapterRegistry, AdapterRegistryError
from .runtime import Runtime
from .supervisor import Supervisor, SupervisorError, SupervisorStatus

DAEMON_VERSION = "0.1.0"

__all__ = [
    "AdapterRegistry",
    "AdapterRegistryError",
    "DAEMON_VERSION",
    "Runtime",
    "Supervisor",
    "SupervisorError",
    "SupervisorStatus",
]
