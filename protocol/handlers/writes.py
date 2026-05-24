"""Server-side handlers for ``write.*`` protocol methods (Phase 2c-2b / 2c-6a).

This module wires the **telemetry-only** write surface — operations that
record PostHog events but do not mutate the ledger:

- ``write.feedback`` → ``handlers.feedback.handle_feedback``
- ``write.skill_begin`` → ``handlers.skill.handle_skill_begin``
- ``write.skill_end`` → ``handlers.skill.handle_skill_end``

**Ledger writes (``write.ingest``, ``write.link_commit``) are NOT registered
here.** Those are owned by the Runtime via the adapter-registry dispatch path:
``Runtime._handle_ingest`` → ``MCPIngestAdapter.ingest`` →
``_handle_ingest_impl`` (Phase 2c-6b). Registering them here would overwrite
the adapter-routing handler and break the registry dispatch contract tested in
``tests/test_daemon_runtime_dispatch.py``.

Same pattern as ``protocol/handlers/reads.py``: validate payload, delegate
to the existing in-tree handler, serialize the response.

Telemetry side-effects (``record_skill_event``, ``send_event``) are
fire-and-forget in the underlying handlers — no special test handling
required beyond confirming the dispatcher returns the typed result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from protocol.contracts import (
    ConnectionContext,
    FeedbackRequest,
    FeedbackResult,
    SkillBeginRequest,
    SkillBeginResult,
    SkillEndRequest,
    SkillEndResult,
)

if TYPE_CHECKING:
    from protocol.server import ProtocolServer


async def handle_write_feedback(params: dict[str, Any], _ctx: ConnectionContext) -> dict[str, Any]:
    req = FeedbackRequest.model_validate(params)
    # Phase 2c-6a: call _handle_feedback_impl directly (not the facade) to
    # avoid an infinite RPC loop: facade → daemon → this dispatcher → facade → …
    from handlers.feedback import _handle_feedback_impl

    raw = await _handle_feedback_impl(
        server_version=req.server_version,
        skill=req.skill,
        trying_to=req.trying_to,
        attempted=req.attempted,
        stuck_on=req.stuck_on,
    )
    return FeedbackResult.model_validate(raw).model_dump()


async def handle_write_skill_begin(
    params: dict[str, Any], _ctx: ConnectionContext
) -> dict[str, Any]:
    req = SkillBeginRequest.model_validate(params)
    # Phase 2c-6a: call _handle_skill_begin_impl directly (not the facade) to
    # avoid an infinite RPC loop: facade → daemon → this dispatcher → facade → …
    from handlers.skill import _handle_skill_begin_impl

    raw = await _handle_skill_begin_impl(
        session_id=req.session_id,
        skill_name=req.skill_name,
    )
    return SkillBeginResult.model_validate(raw).model_dump()


async def handle_write_skill_end(params: dict[str, Any], _ctx: ConnectionContext) -> dict[str, Any]:
    req = SkillEndRequest.model_validate(params)
    # Phase 2c-6a: call _handle_skill_end_impl directly (not the facade) to
    # avoid an infinite RPC loop: facade → daemon → this dispatcher → facade → …
    from handlers.skill import _handle_skill_end_impl

    raw = await _handle_skill_end_impl(
        session_id=req.session_id,
        skill_name=req.skill_name,
        server_version=req.server_version,
        errored=req.errored,
        error_class=req.error_class,
        diagnostic=req.diagnostic,
    )
    # ``_handle_skill_end_impl`` returns a dict whose ``diagnostic_warning`` key is
    # only present on validation failure — Pydantic's ``extra="ignore"`` plus
    # ``Optional`` field handles both shapes.
    return SkillEndResult.model_validate(raw).model_dump()


def register_write_handlers(server: ProtocolServer) -> None:
    """Register every ``write.*`` telemetry method on ``server``.

    Idempotent: re-registering overwrites the existing handler.

    Note: ``write.ingest`` and ``write.link_commit`` are intentionally
    absent here — they are registered by ``Runtime._register_default_methods``
    via the adapter-registry dispatch (``Runtime._handle_ingest`` /
    ``Runtime._handle_link_commit`` → ``MCPIngestAdapter``). Registering them
    here would overwrite the adapter-routing handler.
    """
    server.register("write.feedback", handle_write_feedback)
    server.register("write.skill_begin", handle_write_skill_begin)
    server.register("write.skill_end", handle_write_skill_end)
