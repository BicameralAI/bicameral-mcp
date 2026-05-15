"""``NotificationsConfig`` — operator config for the notification layer.

Reads ``~/.bicameral/notifications.yml`` (or operator-supplied path).
Fail-closed on missing file / malformed YAML / no ``notification_policy``
key — returns an empty config + stderr warning. Mirrors the
``_read_team_config`` pattern at ``adapters/ledger.py:25-50``.

Config shape::

    notification_policy:
      channels:
        - type: slack
          webhook_url_env: SLACK_WEBHOOK_URL
          events: [decision_ratified]

The ``events`` list filters which event types trigger a channel.
Adapter-specific keys (``webhook_url_env``, etc.) flow through
``ChannelConfig.extra`` so the registry-driven adapter constructor
can consume them.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_CONFIG_PATH = Path.home() / ".bicameral" / "notifications.yml"


@dataclass(frozen=True)
class ChannelConfig:
    """Parsed config for one notification channel."""

    type: str
    events: tuple[str, ...]
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationsConfig:
    """Operator config for the notifications layer."""

    channels: tuple[ChannelConfig, ...] = field(default_factory=tuple)

    @classmethod
    def load(cls, path: Path | str | None = None) -> NotificationsConfig:
        cfg_path = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
        if not cfg_path.exists():
            return cls()
        try:
            import yaml

            raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — config load must not raise
            print(
                f"[notifications] config at {cfg_path} unreadable: {exc}",
                file=sys.stderr,
            )
            return cls()
        if not isinstance(raw, dict):
            return cls()
        policy = raw.get("notification_policy")
        if not isinstance(policy, dict):
            return cls()
        raw_channels = policy.get("channels") or []
        if not isinstance(raw_channels, list):
            return cls()
        # Import here to avoid module-level cycles.
        from . import CHANNELS

        parsed: list[ChannelConfig] = []
        for raw_channel in raw_channels:
            if not isinstance(raw_channel, dict):
                continue
            ch_type = str(raw_channel.get("type") or "").strip()
            if not ch_type:
                continue
            if ch_type not in CHANNELS:
                print(
                    f"[notifications] unknown channel type {ch_type!r}; skipping.",
                    file=sys.stderr,
                )
                continue
            raw_events = raw_channel.get("events") or []
            if not isinstance(raw_events, list):
                raw_events = []
            events = tuple(str(e) for e in raw_events if isinstance(e, str))
            extra = {k: v for k, v in raw_channel.items() if k not in ("type", "events")}
            parsed.append(ChannelConfig(type=ch_type, events=events, extra=extra))
        return cls(channels=tuple(parsed))
