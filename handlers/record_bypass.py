"""Handler for ``bicameral.record_bypass`` MCP tool (#112).

Small write tool exposed to skill context so the bypass option on a
preflight HITL prompt can be persisted from outside the server. The
handler is a thin wrapper around ``preflight_telemetry.write_bypass_event``:

  - Returns ``recorded=True, deduped=False`` on a fresh bypass write.
  - Returns ``recorded=False, deduped=True`` when a prior bypass for
    the same ``decision_id`` is still inside the recency window
    (V4 idempotent guard prevents indefinite escalation suppression).
  - Returns ``recorded=False, deduped=False, reason='telemetry_disabled'``
    when ``BICAMERAL_PREFLIGHT_TELEMETRY`` is off.

Bypass does NOT mutate decision state. The unresolved ``signoff_state``
persists for future preflight surfaces. The governance engine reads
bypass recency at preflight call time and drops one tier on the action
ladder when a recent bypass exists -- acknowledgement that the user
has seen the unresolved state, not a permanent suppression.
"""

from __future__ import annotations

import logging

from contracts import RecordBypassResponse

logger = logging.getLogger(__name__)


async def handle_record_bypass(
    ctx,
    decision_id: str,
    reason: str = "user_bypassed",
    state_preserved: str = "proposed",
) -> RecordBypassResponse:
    """Record that the user bypassed a preflight HITL prompt.

    ``ctx`` is the standard ``BicameralContext`` -- unused here because
    bypass storage lives in the local JSONL log (no ledger write).
    Idempotent within the 1-hour recency window: a second call inside
    the window returns ``deduped=True`` without writing. Caller-side
    skills can rely on ``recorded`` to distinguish a fresh bypass from
    a within-window repeat.
    """
    del ctx  # unused — bypass storage is local JSONL, not the ledger.

    if not decision_id or not isinstance(decision_id, str):
        return RecordBypassResponse(
            recorded=False,
            deduped=False,
            reason="invalid_decision_id",
        )

    # Imported lazily so tests that monkeypatch ``preflight_telemetry``
    # observe the patched module. Otherwise the import freezes at
    # server-startup time and breaks the per-test ``Path.home()``
    # reload pattern used elsewhere in the suite.
    from preflight_telemetry import (
        recent_bypass_seconds,
        telemetry_enabled,
        write_bypass_event,
    )

    if not telemetry_enabled():
        return RecordBypassResponse(
            recorded=False,
            deduped=False,
            reason="telemetry_disabled",
        )

    was_recent = recent_bypass_seconds(decision_id) is not None
    write_bypass_event(
        decision_id,
        reason=reason,
        state_preserved=state_preserved,
    )
    return RecordBypassResponse(
        recorded=not was_recent,
        deduped=was_recent,
        reason=None,
    )
