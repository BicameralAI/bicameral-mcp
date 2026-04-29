"""Handler for /bicameral.list_unclassified_decisions MCP tool (#77).

Read-only. Returns decisions whose ``decision_level`` is NONE alongside a
heuristic-proposed level (L1/L2/L3) and rationale per row. The agent loop
typically reviews each proposal, decides whether to trust the heuristic or
override, then calls ``bicameral.set_decision_level`` per row.
"""

from __future__ import annotations

import logging
from typing import Literal, cast

from classify.heuristic import classify
from contracts import ListUnclassifiedDecisionsResponse, UnclassifiedProposal

logger = logging.getLogger(__name__)


async def handle_list_unclassified_decisions(
    ctx,
    decision_ids: list[str] | None = None,
) -> ListUnclassifiedDecisionsResponse:
    """List decisions with NONE decision_level, each with a heuristic proposal.

    Args:
        ctx: BicameralContext.
        decision_ids: Optional restriction to a specific subset. When None
            or empty, returns every unclassified decision in the ledger.

    Returns:
        ListUnclassifiedDecisionsResponse with proposals[] and total_count.
    """
    ledger = ctx.ledger
    if hasattr(ledger, "connect"):
        await ledger.connect()

    inner = getattr(ledger, "_inner", ledger)
    client = inner._client

    if decision_ids:
        # SurrealDB IN list of record-ids; the helper validates shape on
        # the write path, but for this read we just filter by membership.
        rows = await client.query(
            "SELECT type::string(id) AS decision_id, description "
            "FROM decision "
            "WHERE decision_level = NONE AND type::string(id) IN $ids",
            {"ids": list(decision_ids)},
        )
    else:
        rows = await client.query(
            "SELECT type::string(id) AS decision_id, description "
            "FROM decision WHERE decision_level = NONE"
        )

    proposals: list[UnclassifiedProposal] = []
    for row in rows or []:
        did = row.get("decision_id") or ""
        desc = row.get("description") or ""
        level, rationale = classify(desc)
        confidence: Literal["high", "low"] = (
            "low" if rationale.lower().startswith("low confidence") else "high"
        )
        proposals.append(
            UnclassifiedProposal(
                decision_id=did,
                description=desc,
                proposed_level=cast(Literal["L1", "L2", "L3"], level),
                rationale=rationale,
                confidence=confidence,
            )
        )

    return ListUnclassifiedDecisionsResponse(
        proposals=proposals,
        total_count=len(proposals),
    )
