"""Handler for ``bicameral.evaluate_governance`` MCP tool.

Read-only ad-hoc evaluation: looks up the decision by id, builds a
synthetic ``GovernanceFinding`` from current ledger state, runs the
deterministic engine, and returns the policy result attached to the
finding. No side effects.

The engine itself is pure; the handler does the IO of resolving the
decision row, then composes ``governance.engine.evaluate``. Phase 4
will plumb a real bypass-recency lookup; Phase 3 always passes
``None`` because the bypass-event JSONL writer doesn't exist yet.
"""

from __future__ import annotations

import logging
from typing import Literal, cast

from contracts import EvaluateGovernanceResponse
from governance import config as governance_config
from governance import engine
from governance.contracts import (
    GovernanceFinding,
    GovernanceMetadata,
    derive_governance_metadata,
)

logger = logging.getLogger(__name__)


_VALID_SOURCES = (
    "preflight",
    "drift",
    "resolve_compliance",
    "link_commit",
    "scan_branch",
    "llm_judge",
)


async def handle_evaluate_governance(
    ctx,
    decision_id: str,
    region_id: str | None = None,
    source: str = "manual",
) -> EvaluateGovernanceResponse:
    """Evaluate the deterministic escalation policy for a single
    ``(decision, region)`` pair. Returns the policy result without
    side effects."""
    inner = getattr(ctx.ledger, "_inner", ctx.ledger)
    client = getattr(inner, "_client", None)
    if client is None:
        return EvaluateGovernanceResponse(
            decision_id=decision_id,
            region_id=region_id,
            error="ledger_client_unavailable",
        )

    rows = await client.query(
        f"SELECT decision_level, signoff, status, governance FROM {decision_id} LIMIT 1"
    )
    if not rows:
        return EvaluateGovernanceResponse(
            decision_id=decision_id,
            region_id=region_id,
            error="unknown_decision_id",
        )
    row = rows[0]
    decision_level = row.get("decision_level")
    signoff = row.get("signoff") or {}
    governance_raw = row.get("governance") or None

    explicit_metadata: GovernanceMetadata | None = None
    if isinstance(governance_raw, dict) and governance_raw:
        try:
            explicit_metadata = GovernanceMetadata.model_validate(governance_raw)
        except Exception as exc:
            logger.debug("[evaluate_governance] failed to validate stored metadata: %s", exc)
    metadata = derive_governance_metadata(decision_level, explicit_metadata)

    decision_status = _decision_status_from_row(signoff, row.get("status"))

    # Map the caller-supplied ``source`` string to the finding enum;
    # arbitrary "manual" requests fall back to ``llm_judge`` (the
    # closest catch-all in the GovernanceFinding source enum).
    source_literal = (
        cast(
            Literal[
                "preflight",
                "drift",
                "resolve_compliance",
                "link_commit",
                "scan_branch",
                "llm_judge",
            ],
            source,
        )
        if source in _VALID_SOURCES
        else cast(
            Literal[
                "preflight",
                "drift",
                "resolve_compliance",
                "link_commit",
                "scan_branch",
                "llm_judge",
            ],
            "llm_judge",
        )
    )

    # Conservative synthetic finding: assume the caller is asking
    # "if drift were detected here, what would Bicameral do?". We
    # pick ``possible_drift`` as the neutral starting status — the
    # caller can also pre-build a richer finding via the factories
    # if they have actual signals.
    finding = GovernanceFinding(
        finding_id=_uuid4(),
        decision_id=decision_id,
        region_id=region_id,
        decision_class=metadata.decision_class,
        risk_class=metadata.risk_class,
        escalation_class=metadata.escalation_class,
        source=source_literal,
        semantic_status="possible_drift",
        confidence={},
        explanation="ad-hoc governance evaluation",
        evidence_refs=[],
    )

    cfg = governance_config.load_config()
    policy = engine.evaluate(
        finding=finding,
        metadata=metadata,
        config=cfg,
        decision_status=decision_status,
        bypass_recency_seconds=None,
    )
    finding_with_result = finding.model_copy(update={"policy_result": policy})
    return EvaluateGovernanceResponse(
        decision_id=decision_id,
        region_id=region_id,
        finding=finding_with_result,
        error=None,
    )


def _decision_status_from_row(signoff: dict, status: str | None) -> engine.DecisionStatus:
    """Map a decision row's signoff + pipeline status to the
    ``DecisionStatus`` literal the engine expects.

    Signoff state takes precedence (``ratified`` / ``proposed`` /
    ``rejected`` / ``superseded`` / ``collision_pending`` /
    ``context_pending``); otherwise fall back to a derived view from
    the pipeline status (``ungrounded`` for ungrounded rows, ``active``
    for everything else).
    """
    sf_state = signoff.get("state") if isinstance(signoff, dict) else None
    if sf_state in (
        "ratified",
        "proposed",
        "rejected",
        "superseded",
        "collision_pending",
        "context_pending",
    ):
        return cast(engine.DecisionStatus, sf_state)
    if status == "ungrounded":
        return "ungrounded"
    return "active"


def _uuid4() -> str:
    """Indirected for easier patching in tests."""
    import uuid

    return str(uuid.uuid4())
