"""Handlers for bicameral.skill_begin / bicameral.skill_end.

Skill telemetry bookends — no ledger writes, no sync. Records start time
on begin, computes duration + emits a PostHog event on end with optional
per-skill diagnostic validation.

Extracted from server.py in Phase 2c-1 (#daemon-extraction parent plan
§Phase 2c-1) so every externally-callable handler lives in handlers/ and
carries a categorization decorator.
"""

from __future__ import annotations

import time
from typing import Any

from protocol.categorization import write_tool

# In-process map of session_id → {t0} for skill timing.
# Populated by handle_skill_begin, consumed by handle_skill_end.
_skill_sessions: dict[str, dict[str, Any]] = {}


@write_tool("write.skill_begin")
async def handle_skill_begin(*, session_id: str, skill_name: str) -> dict[str, Any]:
    _skill_sessions[session_id] = {"t0": time.monotonic()}
    return {
        "session_id": session_id,
        "skill": skill_name,
        "status": "started",
    }


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
