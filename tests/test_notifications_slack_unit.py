"""Sociable unit tests for SlackChannelAdapter (#330 + #335 Phase 2a).

The SlackClient HTTP layer is the only seam — tests inject a fake
recording client; the adapter, registry, config flow, and message
rendering all run real. Mirrors `tests/test_sources_granola_unit.py`
testing precedent.
"""

from __future__ import annotations

import pytest

from notifications import (
    CHANNELS,
    ChannelAdapter,
    ChannelDeliveryError,
    NotificationEvent,
    SlackChannelAdapter,
)


class _FakeClient:
    """Records POSTs in-memory; never touches the network."""

    def __init__(self, *, raise_on_post: Exception | None = None) -> None:
        self.posts: list[str] = []
        self._raise = raise_on_post

    def post(self, *, text: str) -> None:
        if self._raise is not None:
            raise self._raise
        self.posts.append(text)


def _event(**overrides) -> NotificationEvent:
    defaults: dict = {
        "event_type": "decision_ratified",
        "decision_id": "decision:abc123",
        "feature_area": "payments",
        "summary": "Ratified by jin",
        "severity": "info",
        "source_ref": "sha:deadbeef",
        "occurred_at": "2026-05-15T02:00:00+00:00",
    }
    defaults.update(overrides)
    return NotificationEvent(**defaults)


# ── registry + protocol ───────────────────────────────────────────────


def test_slack_adapter_registered_in_channels() -> None:
    assert "slack" in CHANNELS
    assert CHANNELS["slack"] is SlackChannelAdapter


def test_slack_adapter_satisfies_channel_adapter_protocol() -> None:
    adapter = SlackChannelAdapter()
    assert isinstance(adapter, ChannelAdapter)


# ── env-var-only secret ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_adapter_reads_webhook_from_env_not_config(monkeypatch) -> None:
    """The webhook URL lives in os.environ; the config holds only the env name."""
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://hooks.slack.example/T/B/X")
    await adapter.deliver(_event())
    assert len(fake.posts) == 1


@pytest.mark.asyncio
async def test_slack_adapter_raises_channel_delivery_error_when_env_missing(
    monkeypatch,
) -> None:
    """No env var → fail-fast with operator-readable error, no HTTP."""
    monkeypatch.delenv("TEST_SLACK_HOOK", raising=False)
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    with pytest.raises(ChannelDeliveryError, match="TEST_SLACK_HOOK"):
        await adapter.deliver(_event())
    assert fake.posts == []


@pytest.mark.asyncio
async def test_slack_default_env_var_name_when_config_omits_webhook_url_env(
    monkeypatch,
) -> None:
    """No config → falls back to SLACK_WEBHOOK_URL."""
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    adapter = SlackChannelAdapter(client=_FakeClient())
    with pytest.raises(ChannelDeliveryError, match="SLACK_WEBHOOK_URL"):
        await adapter.deliver(_event())


# ── message-shape pins ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_deliver_posts_to_webhook(monkeypatch) -> None:
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://example/")
    await adapter.deliver(_event())
    assert len(fake.posts) == 1


@pytest.mark.asyncio
async def test_slack_payload_text_includes_event_type_and_feature_area_and_summary(
    monkeypatch,
) -> None:
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://example/")
    await adapter.deliver(_event(event_type="decision_ratified", feature_area="auth", summary="x"))
    text = fake.posts[0]
    assert "decision_ratified" in text
    assert "auth" in text
    assert "x" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden",
    ["text", "description", "rationale", "speakers", "raw_content"],
)
async def test_slack_payload_contains_no_raw_pii_field_names(monkeypatch, forbidden) -> None:
    """Per #221 boundary: the slack message must NOT carry any of the
    forbidden PII field names. Phase 1 pins the dataclass shape; this
    test reinforces at the wire layer."""
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://example/")
    await adapter.deliver(_event())
    assert forbidden not in fake.posts[0].lower()


# ── HTTP errors ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_deliver_raises_channel_delivery_error_on_http_error(
    monkeypatch,
) -> None:
    fake = _FakeClient(raise_on_post=ConnectionError("network down"))
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://example/")
    with pytest.raises(ChannelDeliveryError, match="Slack POST failed"):
        await adapter.deliver(_event())


# ── 200-char cap holds through delivery ──────────────────────────────


@pytest.mark.asyncio
async def test_slack_deliver_truncates_long_summary_via_event_invariant(
    monkeypatch,
) -> None:
    """Phase 1's 200-char summary cap holds; slack message contains
    the truncated summary, never the raw 500-char input."""
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://example/")
    long = "x" * 500
    await adapter.deliver(_event(summary=long))
    # The event's summary is capped at 200; the slack message
    # carries the truncated version.
    text = fake.posts[0]
    assert "x" * 200 in text
    assert "x" * 201 not in text


# ── operator-note PII responsibility (audit advisory #2) ─────────────


@pytest.mark.asyncio
async def test_slack_payload_renders_operator_note_verbatim_into_summary(
    monkeypatch,
) -> None:
    """The operator-supplied ``note`` on ratify lands in ``summary``
    verbatim (subject to the 200-char cap). Bicameral does NOT scrub
    operator-content; the operator is responsible for not putting
    PII / secrets into ``note``. The notifications-config.md doc
    carries the matching operator-facing warning. Mirrors the
    `acceptable-use.md` ingest-side discipline.
    """
    fake = _FakeClient()
    adapter = SlackChannelAdapter(
        config={"webhook_url_env": "TEST_SLACK_HOOK"},
        client=fake,
    )
    monkeypatch.setenv("TEST_SLACK_HOOK", "https://example/")
    operator_note = "Approved for customer escalation"
    await adapter.deliver(_event(summary=operator_note))
    text = fake.posts[0]
    assert operator_note in text  # verbatim, no scrubbing
