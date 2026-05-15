"""StderrChannelAdapter — smoke-test channel for the notification layer.

Emits a single structured JSON line to stderr per delivered event.
Useful for local-dev validation, CI smoke tests, and as the reference
implementation for future channels (Slack, email, webhook).

Sync at the wire level (no ``await``) but conforms to the async
``ChannelAdapter.deliver`` contract — degenerate ``async def`` so
future network adapters can replace it without a contract break.
"""

from __future__ import annotations

import dataclasses
import json
import sys

from .contracts import ChannelDeliveryError, NotificationEvent


class StderrChannelAdapter:
    """Smoke-test channel — emits one JSON line per event to stderr."""

    name = "stderr"

    async def deliver(self, event: NotificationEvent) -> None:
        try:
            payload = dataclasses.asdict(event)
            line = "[notifications][stderr] " + json.dumps(
                payload, separators=(",", ":"), sort_keys=True
            )
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception as exc:  # noqa: BLE001
            raise ChannelDeliveryError(f"stderr channel failed to deliver: {exc}") from exc
