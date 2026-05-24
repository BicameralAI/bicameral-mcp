"""Server-side handlers for the universal protocol.

In Phase 2c-2 these run **in the same Python process as MCP** — there is no
daemon subprocess yet. They exist to make the protocol's read/write surface
real (every method dispatches to working code), so the call-site migration
in Phase 2c-4 onward has a concrete target.

In Phase 2c-3, when the daemon supervisor lands and spawns a real process,
these handlers run inside the daemon and MCP becomes their only client. The
handler bodies don't change — they already delegate to the existing in-tree
ledger code. Only the deployment topology shifts.
"""

from __future__ import annotations

from .grounding import register_grounding_handlers
from .reads import register_read_handlers
from .writes import register_write_handlers

__all__ = ["register_grounding_handlers", "register_read_handlers", "register_write_handlers"]
