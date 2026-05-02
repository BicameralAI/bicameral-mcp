"""Handler for /bicameral.set_decision_level MCP tool (#77).

Single-row write. Idempotent. Errors are returned as structured
``{ok: false, error: ...}`` responses rather than raised exceptions so an
agent loop can recover per-row without aborting the whole batch.

Uses the same ``ledger.queries.update_decision_level`` primitive as the
bulk-classify CLI (``cli/classify.py``) and the dashboard inline-edit POST
endpoint (sibling PR for #76). One write path, three callers.
"""

from __future__ import annotations

import logging

from contracts import SetDecisionLevelResponse
from ledger.queries import DecisionNotFound, update_decision_level

logger = logging.getLogger(__name__)


async def handle_set_decision_level(
    ctx,
    decision_id: str,
    level: str,
    rationale: str | None = None,
) -> SetDecisionLevelResponse:
    """Set decision_level on a single decision. Idempotent.

    Args:
        ctx: BicameralContext.
        decision_id: Full record id (e.g. ``"decision:abc123"``).
        level: One of ``"L1"``, ``"L2"``, ``"L3"``.
        rationale: Optional one-line audit note. Currently logged only;
            persistence pathway is reserved for a future audit-trail row.

    Returns:
        SetDecisionLevelResponse with ok=True/level on success or
        ok=False/error on validation/lookup failure.
    """
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    try:
        await update_decision_level(client, decision_id, level)
    except ValueError as exc:
        logger.info(
            "[set_decision_level] validation failed: decision=%s level=%s err=%s",
            decision_id,
            level,
            exc,
        )
        return SetDecisionLevelResponse(
            ok=False,
            decision_id=decision_id,
            error=str(exc),
        )
    except DecisionNotFound as exc:
        logger.info(
            "[set_decision_level] decision_id not found: %s",
            exc,
        )
        return SetDecisionLevelResponse(
            ok=False,
            decision_id=decision_id,
            error=f"decision_id not found: {decision_id}",
        )

    if rationale:
        logger.info(
            "[set_decision_level] decision=%s level=%s rationale=%s",
            decision_id,
            level,
            rationale,
        )

    return SetDecisionLevelResponse(
        ok=True,
        decision_id=decision_id,
        level=level,
    )
