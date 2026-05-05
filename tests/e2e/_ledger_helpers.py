"""Pure helpers for ledger-based flow validation.

Extracted from run_e2e_flows.py so unit tests can import without
triggering the harness's top-level env-var / CLI-presence guards.
"""

from __future__ import annotations


def count_agent_session_decisions(snapshot: dict) -> int | None:
    """Count decisions with source_type='agent_session' in a ledger snapshot.

    Returns None if the snapshot reports an error (caller treats as
    INCONCLUSIVE, not FAIL — the assertion is unreliable when the ledger
    isn't queryable). Returns 0 when there are no agent_session rows. The
    'agent_session' source_type is the canonical tag written by both
    in-session capture-corrections (path-A) and the SessionEnd subprocess
    (path-B); this helper does not discriminate between them, only counts
    the product-outcome signal.
    """
    if "error" in snapshot:
        return None
    decisions = snapshot.get("decisions") or []
    return sum(1 for d in decisions if d.get("source_type") == "agent_session")
