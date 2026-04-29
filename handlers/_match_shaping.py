"""Shared row-to-DecisionMatch shaping. Pure value transformation.

Used by ``handle_search_decisions`` (the canonical caller) and
``handle_preflight`` (which calls ``ctx.ledger.search_by_query``
directly to avoid the ``handle_link_commit`` cascade that would
otherwise double-sync per preflight invocation).

Status inference rules (preserved from the original loop body in
search_decisions.py:38-75):
  - if the row's ``status`` is one of the canonical states
    ("reflected", "drifted", "pending", "ungrounded"), pass through
  - otherwise: "ungrounded" when no code_regions, "pending" when present
"""

from __future__ import annotations

from contracts import CodeRegionSummary, DecisionMatch

_KNOWN_STATUSES = ("reflected", "drifted", "pending", "ungrounded")


def _raw_to_decision_match(m: dict) -> DecisionMatch:
    """Shape one raw ledger row into a DecisionMatch.

    Behavior is preserved verbatim from search_decisions.py prior to the
    extraction; the test suite ``tests/test_phase2_ledger.py`` is the
    regression gate for that contract.
    """
    regions = [
        CodeRegionSummary(
            file_path=r["file_path"],
            symbol=r["symbol"],
            lines=tuple(r["lines"]),
            purpose=r.get("purpose", ""),
        )
        for r in m.get("code_regions", [])
    ]
    decision_status = str(m.get("status") or "").strip()
    if decision_status in _KNOWN_STATUSES:
        status = decision_status
    elif not regions:
        status = "ungrounded"
    else:
        status = "pending"
    _signoff = m.get("signoff") or {}
    return DecisionMatch(
        decision_id=m["decision_id"],
        description=m["description"],
        status=status,
        signoff_state=(_signoff.get("state") if isinstance(_signoff, dict) else None),
        confidence=m.get("confidence", 0.5),
        source_ref=m.get("source_ref", ""),
        code_regions=regions,
        drift_evidence=m.get("drift_evidence", ""),
        related_constraints=m.get("related_constraints", []),
        source_excerpt=m.get("source_excerpt", ""),
        meeting_date=m.get("meeting_date", ""),
        signoff=m.get("signoff"),
    )
