"""Sociable unit tests for the notifications channel-adapter foundation
(#330 + #335 Phase 1).

Real Python imports, no mocks. The only "narrow seam" is ``capsys`` for
stderr capture and ``monkeypatch`` for sys.stderr.write injection in
the error-propagation test.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from typing import get_args, get_type_hints

import pytest

from notifications import (
    CHANNELS,
    ChannelAdapter,
    ChannelDeliveryError,
    EventType,
    NotificationEvent,
    Severity,
    StderrChannelAdapter,
)


def _make_event(**overrides) -> NotificationEvent:
    defaults: dict = {
        "event_type": "decision_ratified",
        "decision_id": "decision:abc123",
        "feature_area": "payments",
        "summary": "Decision ratified by jin@bicameral-ai.com",
        "severity": "info",
        "source_ref": "meeting-2026-05-14",
        "occurred_at": "2026-05-14T23:00:00+00:00",
    }
    defaults.update(overrides)
    return NotificationEvent(**defaults)


# ── registry + protocol conformance ───────────────────────────────────


def test_stderr_registered_in_channels() -> None:
    assert "stderr" in CHANNELS
    assert CHANNELS["stderr"] is StderrChannelAdapter


def test_stderr_satisfies_channel_adapter_protocol() -> None:
    adapter = StderrChannelAdapter()
    assert isinstance(adapter, ChannelAdapter)


# ── NotificationEvent contract ────────────────────────────────────────


def test_notification_event_truncates_long_summary_to_200_chars() -> None:
    long = "x" * 500
    event = _make_event(summary=long)
    assert len(event.summary) == 200
    assert event.summary == "x" * 200


def test_notification_event_is_frozen_dataclass() -> None:
    event = _make_event()
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.summary = "mutated"  # type: ignore[misc]


def test_notification_event_severity_is_closed_enum() -> None:
    """The Severity Literal is a closed set; runtime dataclass
    construction with a bad value still works (Python doesn't enforce
    Literal at runtime), but the type system + downstream consumers
    pin the contract. Verify the Literal alias is what we expect."""
    severities = set(get_args(Severity))
    assert severities == {"info", "warn", "error"}


def test_notification_event_event_type_is_closed_enum() -> None:
    """Same shape check for EventType."""
    event_types = set(get_args(EventType))
    expected = {
        "proposal_captured",
        "decision_ratified",
        "decision_rejected",
        "decision_superseded",
        "drift_detected",
        "compliance_recorded",
        "gap_judgment",
        "health_digest",
    }
    assert event_types == expected


def test_notification_event_no_pii_fields_present() -> None:
    """Per the #221 design directive: structural fact only. The
    dataclass must NOT carry any of the listed PII-shaped fields.
    Pins the PII boundary at the contract layer."""
    field_names = {f.name for f in dataclasses.fields(NotificationEvent)}
    forbidden = {"text", "description", "rationale", "speakers", "raw_content"}
    assert field_names.isdisjoint(forbidden), (
        f"NotificationEvent acquired a forbidden PII field: {field_names & forbidden}"
    )


def test_notification_event_carries_only_structural_fields() -> None:
    """Whitelist check — explicit list of accepted fields. Catches
    accidental field additions even if they're not in the forbidden set."""
    field_names = {f.name for f in dataclasses.fields(NotificationEvent)}
    expected = {
        "event_type",
        "decision_id",
        "feature_area",
        "summary",
        "severity",
        "source_ref",
        "occurred_at",
    }
    assert field_names == expected


# ── StderrChannelAdapter behavior ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stderr_deliver_emits_single_json_line_to_stderr(capsys) -> None:
    adapter = StderrChannelAdapter()
    await adapter.deliver(_make_event())
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].startswith("[notifications][stderr] ")


@pytest.mark.asyncio
async def test_stderr_deliver_includes_all_event_fields_in_json(capsys) -> None:
    adapter = StderrChannelAdapter()
    event = _make_event()
    await adapter.deliver(event)
    err = capsys.readouterr().err
    line = err.splitlines()[0]
    payload = json.loads(line.removeprefix("[notifications][stderr] "))
    assert payload["event_type"] == "decision_ratified"
    assert payload["decision_id"] == "decision:abc123"
    assert payload["feature_area"] == "payments"
    assert payload["severity"] == "info"
    assert payload["source_ref"] == "meeting-2026-05-14"
    assert payload["occurred_at"] == "2026-05-14T23:00:00+00:00"
    assert payload["summary"].startswith("Decision ratified")


@pytest.mark.asyncio
async def test_stderr_deliver_emits_valid_json_for_event_with_empty_optional_fields(
    capsys,
) -> None:
    adapter = StderrChannelAdapter()
    event = NotificationEvent(
        event_type="health_digest",
        decision_id=None,
        feature_area="payments",
        summary="weekly digest",
        severity="info",
    )
    await adapter.deliver(event)
    err = capsys.readouterr().err
    line = err.splitlines()[0]
    payload = json.loads(line.removeprefix("[notifications][stderr] "))
    assert payload["decision_id"] is None
    assert payload["source_ref"] == ""
    assert payload["occurred_at"] == ""


def test_stderr_deliver_is_async() -> None:
    adapter = StderrChannelAdapter()
    assert inspect.iscoroutinefunction(adapter.deliver)


# ── error semantics ──────────────────────────────────────────────────


def test_channel_delivery_error_is_runtime_error_subclass() -> None:
    assert issubclass(ChannelDeliveryError, RuntimeError)


@pytest.mark.asyncio
async def test_stderr_deliver_raises_channel_delivery_error_on_write_failure(
    monkeypatch,
) -> None:
    """Per the fail-isolation contract: an adapter must raise a
    ``ChannelDeliveryError`` rather than silently dropping the event.
    Callers (Phase 2's fan-out loop) catch and log."""
    import sys

    def _boom(*_args, **_kwargs):
        raise OSError("simulated stderr write failure")

    monkeypatch.setattr(sys.stderr, "write", _boom)
    adapter = StderrChannelAdapter()
    with pytest.raises(ChannelDeliveryError) as excinfo:
        await adapter.deliver(_make_event())
    assert "stderr channel failed" in str(excinfo.value)


# ── parametrized: each EventType round-trips through stderr ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", list(get_args(EventType)))
async def test_stderr_deliver_for_each_event_type(capsys, event_type) -> None:
    adapter = StderrChannelAdapter()
    event = _make_event(event_type=event_type, summary=f"event of type {event_type}")
    await adapter.deliver(event)
    err = capsys.readouterr().err
    line = err.splitlines()[0]
    payload = json.loads(line.removeprefix("[notifications][stderr] "))
    assert payload["event_type"] == event_type


# ── type-system sanity ───────────────────────────────────────────────


def test_notification_event_type_hints_reference_severity_literal() -> None:
    """Verify ``severity`` field's annotation IS the ``Severity`` Literal —
    catches accidental field-type drift (e.g., someone widening it to ``str``)."""
    hints = get_type_hints(NotificationEvent)
    # Severity is a Literal["info","warn","error"]; the type hint should be
    # that exact alias (resolved via get_type_hints).
    assert hints["severity"] == Severity
