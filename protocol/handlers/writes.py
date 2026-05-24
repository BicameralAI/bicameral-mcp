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
    JudgeGapsRequest,
    JudgeGapsResult,
    RatifyRequest,
    RatifyResult,
    ResolveCollisionRequest,
    ResolveCollisionResult,
    ResolveComplianceRequest,
    ResolveComplianceResult,
    SkillBeginRequest,
    SkillBeginResult,
    SkillEndRequest,
    SkillEndResult,
)

if TYPE_CHECKING:
    from protocol.server import ProtocolServer


def _resolve_context(conn_ctx: ConnectionContext, repo_id: str):
    """Build a ``BicameralContext`` for a write protocol handler.

    Phase 2c-6c shim: mirrors the read-side ``_resolve_context`` in
    ``protocol/handlers/reads.py``. Multi-repo resolution lands in 2c-3;
    for now we always return ``BicameralContext.from_env()``.
    """
    from context import BicameralContext

    return BicameralContext.from_env()


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


async def handle_write_ratify(params: dict[str, Any], ctx: ConnectionContext) -> dict[str, Any]:
    req = RatifyRequest.model_validate(params)
    bctx = _resolve_context(ctx, req.repo_id)
    # Call _handle_ratify_impl directly (not the facade) to avoid an
    # infinite RPC loop: facade → daemon → this dispatcher → facade → …
    from handlers.ratify import _handle_ratify_impl

    raw = await _handle_ratify_impl(
        ctx=bctx,
        decision_id=req.decision_id,
        signer=req.signer,
        note=req.note,
        action=req.action,
        preflight_id=req.preflight_id,
    )
    return RatifyResult.model_validate(raw).model_dump()


async def handle_write_resolve_compliance(
    params: dict[str, Any], ctx: ConnectionContext
) -> dict[str, Any]:
    req = ResolveComplianceRequest.model_validate(params)
    bctx = _resolve_context(ctx, req.repo_id)
    # Call _handle_resolve_compliance_impl directly to avoid the RPC loop.
    from handlers.resolve_compliance import _handle_resolve_compliance_impl

    raw = await _handle_resolve_compliance_impl(
        ctx=bctx,
        phase=req.phase,
        verdicts=req.verdicts,
        commit_hash=req.commit_hash,
        flow_id=req.flow_id,
    )
    return ResolveComplianceResult.model_validate(raw).model_dump()


async def handle_write_resolve_collision(
    params: dict[str, Any], ctx: ConnectionContext
) -> dict[str, Any]:
    req = ResolveCollisionRequest.model_validate(params)
    bctx = _resolve_context(ctx, req.repo_id)
    # Call _handle_resolve_collision_impl directly to avoid the RPC loop.
    from handlers.resolve_collision import _handle_resolve_collision_impl

    raw = await _handle_resolve_collision_impl(
        ctx=bctx,
        new_id=req.new_id,
        old_id=req.old_id,
        action=req.action,
        span_id=req.span_id,
        decision_id=req.decision_id,
        confirmed=req.confirmed,
    )
    return ResolveCollisionResult.model_validate(raw).model_dump()


async def handle_write_judge_gaps(params: dict[str, Any], ctx: ConnectionContext) -> dict[str, Any]:
    req = JudgeGapsRequest.model_validate(params)
    bctx = _resolve_context(ctx, req.repo_id)
    # Call _handle_judge_gaps_impl directly to avoid the RPC loop.
    from handlers.gap_judge import _handle_judge_gaps_impl

    raw = await _handle_judge_gaps_impl(
        ctx=bctx,
        topic=req.topic,
        max_decisions=req.max_decisions,
    )
    return JudgeGapsResult.model_validate(raw).model_dump()


def register_write_handlers(server: ProtocolServer) -> None:
    """Register every ``write.*`` method on ``server``.

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
    # Mutation writes (Phase 2c-6c)
    server.register("write.ratify", handle_write_ratify)
    server.register("write.resolve_compliance", handle_write_resolve_compliance)
    server.register("write.resolve_collision", handle_write_resolve_collision)
    server.register("write.judge_gaps", handle_write_judge_gaps)
