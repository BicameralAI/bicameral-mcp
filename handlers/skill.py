"""Handlers for bicameral.skill_begin / bicameral.skill_end.

Skill telemetry bookends — no ledger writes, no sync. Records start time
on begin, computes duration + emits a PostHog event on end with optional
per-skill diagnostic validation.

Extracted from server.py in Phase 2c-1 (#daemon-extraction parent plan
§Phase 2c-1) so every externally-callable handler lives in handlers/ and
carries a categorization decorator.

Phase 2c-6a: split into MCP-side facades (handle_skill_begin /
handle_skill_end) that delegate to the daemon when available, and
pure-impl cores (_handle_skill_begin_impl / _handle_skill_end_impl) that
both the facade's fallback path and the daemon's server-side dispatcher
(protocol/handlers/writes.py) call directly to avoid infinite RPC loops.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from protocol.categorization import write_tool

logger = logging.getLogger(__name__)

# In-process map of session_id → {t0} for skill timing.
# Populated by _handle_skill_begin_impl, consumed by _handle_skill_end_impl.
_skill_sessions: dict[str, dict[str, Any]] = {}


async def _handle_skill_begin_impl(*, session_id: str, skill_name: str) -> dict[str, Any]:
    """Core skill_begin logic — records t0 in _skill_sessions.

    Invoked by the daemon's ``write.skill_begin`` protocol handler and by
    the MCP-side facade when the daemon is not reachable.
    """
    _skill_sessions[session_id] = {"t0": time.monotonic()}
    return {
        "session_id": session_id,
        "skill": skill_name,
        "status": "started",
    }


async def _handle_skill_end_impl(
    *,
    session_id: str,
    skill_name: str,
    server_version: str,
    errored: bool = False,
    error_class: str | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Core skill_end logic — pops t0, emits PostHog event.

    Invoked by the daemon's ``write.skill_end`` protocol handler and by
    the MCP-side facade when the daemon is not reachable.
    """
    from pydantic import ValidationError

    from contracts import SKILL_DIAGNOSTIC_MODELS
    from telemetry import record_skill_event

    raw_diagnostic = diagnostic or {}
    session_data = _skill_sessions.pop(session_id, None)
    t0 = session_data["t0"] if session_data else None
    duration_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else 0

    # Validate diagnostic against the per-skill Pydantic model.
    # On unknown fields: record the clean validated dict to PostHog and
    # echo unknown field names back so the LLM can correct them.
    diagnostic_model = SKILL_DIAGNOSTIC_MODELS.get(skill_name)
    unknown_fields: list[str] = []
    validated_diagnostic: dict[str, Any] | None
    if diagnostic_model and raw_diagnostic:
        try:
            validated = diagnostic_model.model_validate(raw_diagnostic)
            validated_diagnostic = validated.model_dump()
        except ValidationError as exc:
            unknown_fields = [
                str(e["loc"][0])
                for e in exc.errors()
                if e["type"] == "extra_forbidden" and e["loc"]
            ]
            known_raw = {k: v for k, v in raw_diagnostic.items() if k not in unknown_fields}
            try:
                validated = diagnostic_model.model_validate(known_raw)
                validated_diagnostic = validated.model_dump()
            except ValidationError:
                validated_diagnostic = known_raw
    else:
        validated_diagnostic = raw_diagnostic or None

    record_skill_event(
        skill_name,
        session_id,
        duration_ms,
        errored,
        server_version,
        diagnostic=validated_diagnostic,
        error_class=error_class,
    )
    response: dict[str, Any] = {
        "session_id": session_id,
        "skill": skill_name,
        "duration_ms": duration_ms,
        "status": "recorded",
    }
    if unknown_fields:
        response["diagnostic_warning"] = (
            f"Unknown diagnostic field(s) were dropped and not recorded: "
            f"{unknown_fields}. Use the exact field names from the skill spec."
        )
    return response


@write_tool("write.skill_begin")
async def handle_skill_begin(*, session_id: str, skill_name: str) -> dict[str, Any]:
    """MCP-side facade for ``write.skill_begin``.

    Phase 2c-6a — if a daemon proxy is available via BicameralContext,
    delegate to it. Otherwise fall through to _handle_skill_begin_impl.
    """
    try:
        from context import BicameralContext

        ctx = BicameralContext.from_env()
        daemon = getattr(ctx, "daemon", None)
    except Exception:
        daemon = None

    if daemon is not None:
        try:
            from protocol.contracts import SkillBeginResult

            raw = await daemon.skill_begin(
                session_id=session_id,
                skill_name=skill_name,
            )
            return SkillBeginResult.model_validate(raw).model_dump()
        except Exception:
            logger.debug(
                "[handle_skill_begin] daemon call failed, falling through to in-process impl",
                exc_info=True,
            )

    return await _handle_skill_begin_impl(session_id=session_id, skill_name=skill_name)


@write_tool("write.skill_end")
async def handle_skill_end(
    *,
    session_id: str,
    skill_name: str,
    server_version: str,
    errored: bool = False,
    error_class: str | None = None,
    diagnostic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """MCP-side facade for ``write.skill_end``.

    Phase 2c-6a — if a daemon proxy is available via BicameralContext,
    delegate to it. Otherwise fall through to _handle_skill_end_impl.
    """
    try:
        from context import BicameralContext

        ctx = BicameralContext.from_env()
        daemon = getattr(ctx, "daemon", None)
    except Exception:
        daemon = None

    if daemon is not None:
        try:
            from protocol.contracts import SkillEndResult

            raw = await daemon.skill_end(
                session_id=session_id,
                skill_name=skill_name,
                server_version=server_version,
                errored=errored,
                error_class=error_class,
                diagnostic=diagnostic,
            )
            return SkillEndResult.model_validate(raw).model_dump()
        except Exception:
            logger.debug(
                "[handle_skill_end] daemon call failed, falling through to in-process impl",
                exc_info=True,
            )

    return await _handle_skill_end_impl(
        session_id=session_id,
        skill_name=skill_name,
        server_version=server_version,
        errored=errored,
        error_class=error_class,
        diagnostic=diagnostic,
    )
