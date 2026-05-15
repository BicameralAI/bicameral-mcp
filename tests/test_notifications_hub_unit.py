"""Sociable unit tests for NotificationHub (#330 + #335 Phase 2a)."""

from __future__ import annotations

import pytest

from notifications import (
    NotificationEvent,
    NotificationHub,
    NotificationsConfig,
    get_hub,
    reset_hub_for_testing,
)
from notifications.config import ChannelConfig


class _RecordingAdapter:
    """Test adapter — records deliver() calls; never networks."""

    name = "recording"

    def __init__(
        self, *, config: dict | None = None, raise_on_deliver: Exception | None = None
    ) -> None:
        self.calls: list[NotificationEvent] = []
        self._raise = raise_on_deliver

    async def deliver(self, event: NotificationEvent) -> None:
        if self._raise is not None:
            raise self._raise
        self.calls.append(event)


def _event(**overrides) -> NotificationEvent:
    defaults: dict = {
        "event_type": "decision_ratified",
        "decision_id": "decision:abc",
        "feature_area": "payments",
        "summary": "test",
        "severity": "info",
    }
    defaults.update(overrides)
    return NotificationEvent(**defaults)


# ── empty config ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hub_with_empty_config_notify_returns_zero() -> None:
    hub = NotificationHub(NotificationsConfig())
    assert await hub.notify(_event()) == 0


# ── fan-out ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hub_fans_to_subscribed_channel(monkeypatch) -> None:
    """Channel subscribed to decision_ratified receives notify calls."""
    import notifications

    monkeypatch.setitem(notifications.CHANNELS, "recording", _RecordingAdapter)
    cfg = NotificationsConfig(
        channels=(ChannelConfig(type="recording", events=("decision_ratified",), extra={}),)
    )
    hub = NotificationHub(cfg)
    succeeded = await hub.notify(_event(event_type="decision_ratified"))
    assert succeeded == 1


@pytest.mark.asyncio
async def test_hub_skips_channels_not_subscribed_to_event_type(monkeypatch) -> None:
    import notifications

    monkeypatch.setitem(notifications.CHANNELS, "recording", _RecordingAdapter)
    cfg = NotificationsConfig(
        channels=(ChannelConfig(type="recording", events=("proposal_captured",), extra={}),)
    )
    hub = NotificationHub(cfg)
    succeeded = await hub.notify(_event(event_type="decision_ratified"))
    assert succeeded == 0


@pytest.mark.asyncio
async def test_hub_empty_events_filter_fires_for_every_event_type(monkeypatch) -> None:
    """``events: ()`` means "no filter" — channel receives every event."""
    import notifications

    monkeypatch.setitem(notifications.CHANNELS, "recording", _RecordingAdapter)
    cfg = NotificationsConfig(channels=(ChannelConfig(type="recording", events=(), extra={}),))
    hub = NotificationHub(cfg)
    assert await hub.notify(_event(event_type="decision_ratified")) == 1
    assert await hub.notify(_event(event_type="drift_detected")) == 1


# ── fail-isolation ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hub_continues_after_one_channel_raises(monkeypatch) -> None:
    """If channel A raises, channel B still receives the event."""

    class _RaiseAdapter:
        name = "raise"

        def __init__(self, *, config: dict | None = None) -> None:
            pass

        async def deliver(self, event: NotificationEvent) -> None:
            raise RuntimeError("boom")

    import notifications

    monkeypatch.setitem(notifications.CHANNELS, "raise", _RaiseAdapter)
    monkeypatch.setitem(notifications.CHANNELS, "recording", _RecordingAdapter)
    cfg = NotificationsConfig(
        channels=(
            ChannelConfig(type="raise", events=(), extra={}),
            ChannelConfig(type="recording", events=(), extra={}),
        )
    )
    hub = NotificationHub(cfg)
    succeeded = await hub.notify(_event())
    # 1 channel succeeded (recording), the other raised
    assert succeeded == 1


@pytest.mark.asyncio
async def test_hub_logs_to_stderr_when_channel_raises(monkeypatch, capsys) -> None:
    class _RaiseAdapter:
        name = "raise"

        def __init__(self, *, config: dict | None = None) -> None:
            pass

        async def deliver(self, event: NotificationEvent) -> None:
            raise RuntimeError("simulated failure")

    import notifications

    monkeypatch.setitem(notifications.CHANNELS, "raise", _RaiseAdapter)
    cfg = NotificationsConfig(channels=(ChannelConfig(type="raise", events=(), extra={}),))
    hub = NotificationHub(cfg)
    await hub.notify(_event())
    err = capsys.readouterr().err
    assert "raise delivery failed" in err
    assert "simulated failure" in err


# ── adapter construction failures ───────────────────────────────────


@pytest.mark.asyncio
async def test_hub_skips_channel_with_unknown_type_during_init(monkeypatch, capsys) -> None:
    """Defensive: if a channel type lands in config but is missing
    from the registry (registry drift), hub skips with stderr warning."""
    import notifications

    # Remove if present, then construct cfg referencing it.
    original = notifications.CHANNELS.pop("slack", None)
    try:
        cfg = NotificationsConfig(channels=(ChannelConfig(type="slack", events=(), extra={}),))
        hub = NotificationHub(cfg)
        succeeded = await hub.notify(_event())
        assert succeeded == 0
        err = capsys.readouterr().err
        assert "unknown channel type" in err
    finally:
        if original is not None:
            notifications.CHANNELS["slack"] = original


@pytest.mark.asyncio
async def test_hub_skips_channel_when_adapter_construction_raises(monkeypatch, capsys) -> None:
    class _BadInitAdapter:
        name = "bad_init"

        def __init__(self, *, config: dict | None = None) -> None:
            raise RuntimeError("init failed")

        async def deliver(self, event: NotificationEvent) -> None: ...

    import notifications

    monkeypatch.setitem(notifications.CHANNELS, "bad_init", _BadInitAdapter)
    cfg = NotificationsConfig(channels=(ChannelConfig(type="bad_init", events=(), extra={}),))
    hub = NotificationHub(cfg)
    succeeded = await hub.notify(_event())
    assert succeeded == 0
    err = capsys.readouterr().err
    assert "failed to construct" in err


# ── singleton + reset ───────────────────────────────────────────────


def test_get_hub_returns_singleton() -> None:
    reset_hub_for_testing()
    h1 = get_hub()
    h2 = get_hub()
    assert h1 is h2
    reset_hub_for_testing()


def test_reset_hub_for_testing_drops_singleton() -> None:
    reset_hub_for_testing()
    h1 = get_hub()
    reset_hub_for_testing()
    h2 = get_hub()
    assert h1 is not h2
    reset_hub_for_testing()
