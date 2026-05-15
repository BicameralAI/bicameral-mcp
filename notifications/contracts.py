"""Typed contracts for the notification-channel layer (#330 + #335).

This is the shared abstraction both feature epics build on. Phase 1
ships only the contracts + protocol + a smoke-test ``stderr`` channel;
event-hub wiring (#330) and health-monitor digest delivery (#335)
arrive in subsequent cycles.

PII boundary (per #221 design directive): ``NotificationEvent`` carries
**structural fact only** — decision_id, event_type, feature_area, a
≤200-char summary, severity, and an opaque source_ref. Never raw
transcript text, decision description, rationale, or speaker names.
Operators wanting raw content downstream of an event build it later
from ``decision_id`` lookup, with the explicit knowledge that they
cross the same data-segregation boundary documented in
``docs/policies/gdpr-art-17-erasure-roadmap.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["info", "warn", "error"]

EventType = Literal[
    "proposal_captured",
    "decision_ratified",
    "decision_rejected",
    "decision_superseded",
    "drift_detected",
    "compliance_recorded",
    "gap_judgment",
    "health_digest",
]


_SUMMARY_MAX_LEN = 200


class ChannelDeliveryError(RuntimeError):
    """Raised by a ``ChannelAdapter`` when delivery fails.

    Callers MUST catch and log this; a single channel's failure must
    NEVER block fan-out to other channels. The eventual registry-driven
    fan-out loop (Phase 2) owns the catch-and-log; Phase 1 pins the
    contract via tests.
    """


@dataclass(frozen=True)
class NotificationEvent:
    """Outbound delivery payload.

    Structural fact only — see module docstring's PII boundary note.
    ``summary`` is truncated to 200 chars at construction so adapters
    don't have to defensively truncate.
    """

    event_type: EventType
    decision_id: str | None
    feature_area: str
    summary: str
    severity: Severity
    source_ref: str = ""
    occurred_at: str = ""

    def __post_init__(self) -> None:
        # Frozen dataclasses need ``object.__setattr__`` to mutate;
        # truncation is the one allowed post-init invariant.
        if len(self.summary) > _SUMMARY_MAX_LEN:
            object.__setattr__(self, "summary", self.summary[:_SUMMARY_MAX_LEN])


# Re-export field for downstream tooling that introspects the dataclass.
__all__ = [
    "ChannelDeliveryError",
    "EventType",
    "NotificationEvent",
    "Severity",
    "field",
]
