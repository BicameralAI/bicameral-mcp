"""Server-side handlers for ``read.*`` protocol methods.

Phase 2c-2 — these are thin adapters between the JSON-RPC dispatcher and
today's in-tree MCP handlers. The daemon validates the request payload via
Pydantic, builds a ``BicameralContext`` (single-repo for now — multi-repo
resolution lands in 2c-3), delegates to the existing handler, and serializes
the response.

No MCP handler bodies are modified by this module. The call-site migration —
where MCP handlers stop importing ``handlers.history`` directly and instead
go through ``ProtocolClient`` — happens in Phase 2c-4 onward.

Constraint (bicameral preflight, decision:wndwxgam2m8yjor0igya):
``read.*`` handlers must remain deterministic. No LLM imports, no LLM calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from protocol.contracts import (
    ConnectionContext,
    HistoryRequest,
    UsageSummaryRequest,
    UsageSummaryResult,
)

if TYPE_CHECKING:
    from context import BicameralContext
    from protocol.server import ProtocolServer


def _resolve_context(_ctx: ConnectionContext, _repo_id: str) -> BicameralContext:
    """Resolve the legacy ``BicameralContext`` for a protocol request.

    Phase 2c-2 shim: ignores ``ConnectionContext.tenant_id`` and
    ``request.repo_id`` and returns ``BicameralContext.from_env()`` — the
    same context MCP builds today. Multi-repo resolution (looking up the
    repo by ID and constructing a context against the right ledger
    instance) lands in 2c-3 when the daemon is genuinely separate from
    MCP and can host multiple repos in one process.

    The argument names already reflect the eventual shape; only the body
    needs to change.
    """
    from context import BicameralContext

    return BicameralContext.from_env()


async def handle_read_history(params: dict[str, Any], ctx: ConnectionContext) -> dict[str, Any]:
    req = HistoryRequest.model_validate(params)
    bctx = _resolve_context(ctx, req.repo_id)
    from handlers.history import handle_history

    result = await handle_history(
        bctx,
        feature_filter=req.feature_filter,
        include_superseded=req.include_superseded,
        include_pruned=req.include_pruned,
        as_of=req.as_of,
    )
    return result.model_dump()


async def handle_read_usage_summary(
    params: dict[str, Any], ctx: ConnectionContext
) -> dict[str, Any]:
    req = UsageSummaryRequest.model_validate(params)
    bctx = _resolve_context(ctx, req.repo_id)
    from handlers.usage_summary import handle_usage_summary

    raw = await handle_usage_summary(bctx, days=req.days)
    # ``handle_usage_summary`` returns a plain dict; round-trip it through the
    # typed result so the wire shape is enforced.
    return UsageSummaryResult.model_validate(raw).model_dump()


def register_read_handlers(server: ProtocolServer) -> None:
    """Register every ``read.*`` method on ``server``.

    Idempotent: re-registering overwrites the existing handler. Test
    fixtures may call this against a fresh server in each test.
    """
    server.register("read.history", handle_read_history)
    server.register("read.usage_summary", handle_read_usage_summary)
