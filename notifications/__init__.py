"""Outbound notification-channel layer (#330 + #335).

Shared abstraction the event-delivery hub (#330) and the health-monitor
digest delivery (#335) build on. Phase 1 ships only the protocol +
registry + a smoke-test ``stderr`` channel.

Future cycles add: Slack adapter, email adapter, webhook adapter,
Linear/Jira adapter, dashboard SSE bridge — each a new class in this
package, registered in ``CHANNELS``.

See ``docs/policies/notifications-roadmap.md`` for the multi-cycle
plan and the explicit "Phase 1 of N; #330 / #335 NOT closed by this
cycle" statement.
"""

from __future__ import annotations

from .channel import ChannelAdapter
from .contracts import (
    ChannelDeliveryError,
    EventType,
    NotificationEvent,
    Severity,
)
from .stderr import StderrChannelAdapter

# Registry — config ``type`` string → adapter class. Mirrors
# ``events/sources/__init__.py::ADAPTERS``.
CHANNELS: dict[str, type] = {
    "stderr": StderrChannelAdapter,
}

__all__ = [
    "CHANNELS",
    "ChannelAdapter",
    "ChannelDeliveryError",
    "EventType",
    "NotificationEvent",
    "Severity",
    "StderrChannelAdapter",
]
