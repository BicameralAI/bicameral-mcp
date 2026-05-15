"""``NotificationHub`` — fan-out orchestrator for the notification layer.

Instantiates one adapter per ``ChannelConfig`` via the ``CHANNELS``
registry. ``notify(event)`` iterates subscribed channels (those whose
``events`` list contains ``event.event_type``), awaits each adapter's
``deliver()`` inside a broad try/except, and returns the count of
successful deliveries.

Fail-isolation discipline (per Phase 1's contract):
- One channel's ``ChannelDeliveryError`` MUST NOT block other channels.
- Unexpected exceptions from an adapter are logged + counted as failure;
  again, never propagate to the caller.
- The handler-side call (``handlers/ratify.py``) wraps ``notify()`` in
  its OWN try/except as belt-and-suspenders — a hub construction
  failure (rare, since config-load is fail-closed) must not block the
  ratify return.
"""

from __future__ import annotations

import logging
import sys

from .config import ChannelConfig, NotificationsConfig
from .contracts import NotificationEvent

logger = logging.getLogger(__name__)


class NotificationHub:
    """Routes ``NotificationEvent``s to subscribed channel adapters."""

    def __init__(self, config: NotificationsConfig) -> None:
        from . import CHANNELS

        self._channels: list[tuple[ChannelConfig, object]] = []
        for ch_cfg in config.channels:
            adapter_cls = CHANNELS.get(ch_cfg.type)
            if adapter_cls is None:
                # Config-parse already filters unknown types, but this
                # is the belt against future divergence between
                # config-parse and registry state.
                print(
                    f"[notifications] hub: unknown channel type {ch_cfg.type!r}; skipping.",
                    file=sys.stderr,
                )
                continue
            try:
                adapter = adapter_cls(config=ch_cfg.extra)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[notifications] hub: channel {ch_cfg.type!r} failed to construct: {exc}",
                    file=sys.stderr,
                )
                continue
            self._channels.append((ch_cfg, adapter))

    async def notify(self, event: NotificationEvent) -> int:
        """Fan ``event`` out to every subscribed channel.

        Returns the count of channels that delivered successfully.
        Never raises — failures log to stderr and are counted as
        zero-success."""
        succeeded = 0
        for ch_cfg, adapter in self._channels:
            if ch_cfg.events and event.event_type not in ch_cfg.events:
                continue
            try:
                await adapter.deliver(event)
                succeeded += 1
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[notifications] {ch_cfg.type} delivery failed: {exc}",
                    file=sys.stderr,
                )
        return succeeded
