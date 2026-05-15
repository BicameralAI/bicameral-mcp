"""``ChannelAdapter`` protocol — the duck-typed contract every outbound
notification channel implements.

Mirrors ``events.sources.SourceAdapter`` (Protocol + ``@runtime_checkable``)
rather than ``events.backends.BackendAdapter`` (ABC). Channels are
pluggable destinations, not abstract-base contracts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .contracts import NotificationEvent


@runtime_checkable
class ChannelAdapter(Protocol):
    """Outbound delivery channel for ``NotificationEvent``s.

    ``name`` is the lookup key into ``notifications.CHANNELS``.
    ``deliver`` is async to accommodate future network adapters
    (Slack, email, webhook). A delivery that fails should raise
    ``ChannelDeliveryError``; never silently swallow.
    """

    name: str

    async def deliver(  # pragma: no cover - protocol
        self, event: NotificationEvent
    ) -> None: ...
