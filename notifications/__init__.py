"""Outbound notification-channel layer (#330 + #335).

Shared abstraction the event-delivery hub (#330) and the health-monitor
digest delivery (#335) build on. Phase 2a ships:
  - ``ChannelAdapter`` protocol + registry (from Phase 1)
  - ``StderrChannelAdapter`` smoke-test channel (from Phase 1)
  - ``SlackChannelAdapter`` — first real network channel
  - ``NotificationsConfig`` + ``NotificationHub`` — operator config +
    fan-out orchestrator
  - ``get_hub()`` process-singleton accessor
  - Wiring at ``handlers/ratify.py`` for ``decision_ratified`` event

Future cycles add: email / webhook / Linear/Jira / dashboard channels,
remaining event types (proposal_captured, drift_detected, etc.),
event filtering by ``feature_area`` / ``min_severity`` / role-defaults.

See ``docs/policies/notifications-roadmap.md`` for the multi-cycle
plan and the explicit "Phase 2a of N; #330 / #335 NOT closed by this
cycle" statement.
"""

from __future__ import annotations

from .channel import ChannelAdapter
from .config import ChannelConfig, NotificationsConfig
from .contracts import (
    ChannelDeliveryError,
    EventType,
    NotificationEvent,
    Severity,
)
from .hub import NotificationHub
from .slack import SlackChannelAdapter, SlackClient
from .stderr import StderrChannelAdapter

# Registry — config ``type`` string → adapter class. Mirrors
# ``events/sources/__init__.py::ADAPTERS``.
CHANNELS: dict[str, type] = {
    "stderr": StderrChannelAdapter,
    "slack": SlackChannelAdapter,
}


# Process-singleton hub. Mirrors ``adapters/ledger.py::get_ledger()``
# pattern — lazy-init on first call; ``reset_hub_for_testing()`` clears.
_hub: NotificationHub | None = None


def get_hub() -> NotificationHub:
    """Return the process-singleton ``NotificationHub``.

    Config loads on first call from ``~/.bicameral/notifications.yml``
    (or the explicit path passed to ``NotificationsConfig.load``).
    Subsequent calls return the same instance.
    """
    global _hub
    if _hub is None:
        _hub = NotificationHub(NotificationsConfig.load())
    return _hub


def reset_hub_for_testing() -> None:
    """Drop the singleton — tests only."""
    global _hub
    _hub = None


__all__ = [
    "CHANNELS",
    "ChannelAdapter",
    "ChannelConfig",
    "ChannelDeliveryError",
    "EventType",
    "NotificationEvent",
    "NotificationHub",
    "NotificationsConfig",
    "Severity",
    "SlackChannelAdapter",
    "SlackClient",
    "StderrChannelAdapter",
    "get_hub",
    "reset_hub_for_testing",
]
