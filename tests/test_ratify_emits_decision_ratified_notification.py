"""End-to-end tests pinning the ratify → notify wiring (#330 + #335 Phase 2a).

Sociable per CLAUDE.md: real LedgerClient over memory://, real
handle_ratify path, real NotificationEvent construction. The hub is
patched via get_hub() to return a recording fake so we can inspect
what flowed through without actually shipping to Slack.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from contracts import RatifyResponse
from handlers.ratify import handle_ratify
from ledger.adapter import SurrealDBLedgerAdapter
from notifications import NotificationEvent


class _RecordingHub:
    """Stand-in for get_hub() — captures the events that the handler
    constructs without actually delivering anywhere."""

    def __init__(self, *, raise_on_notify: Exception | None = None) -> None:
        self.events: list[NotificationEvent] = []
        self._raise = raise_on_notify

    async def notify(self, event: NotificationEvent) -> int:
        if self._raise is not None:
            raise self._raise
        self.events.append(event)
        return 1


@pytest.fixture
async def fresh_ledger() -> SurrealDBLedgerAdapter:
    adapter = SurrealDBLedgerAdapter(url="memory://")
    await adapter.connect()
    return adapter


async def _seed_decision(ledger: SurrealDBLedgerAdapter) -> str:
    """Create one input_span + decision; return the decision_id."""
    client = ledger._client
    await client.execute(
        """
        CREATE input_span SET
            text = 'we should use the auth proposal',
            source_type = 'manual',
            source_ref = 'test-ref',
            speakers = ['alice@example.com'],
            meeting_date = '2026-05-15'
        """
    )
    await client.execute(
        """
        CREATE decision SET
            description = 'use the auth proposal',
            source_type = 'manual',
            source_ref = 'test-ref',
            speakers = ['alice@example.com'],
            meeting_date = '2026-05-15',
            signoff = { state: 'proposed' }
        """
    )
    rows = await client.query("SELECT type::string(id) AS id FROM decision LIMIT 1")
    return str(rows[0]["id"])


def _ctx(ledger: SurrealDBLedgerAdapter) -> SimpleNamespace:
    return SimpleNamespace(
        ledger=ledger,
        authoritative_sha="sha:deadbeef",
        session_id="test-session",
    )


# ── happy path: ratify fires decision_ratified notification ──────────


@pytest.mark.asyncio
async def test_ratify_action_calls_notify_with_decision_ratified_event_type(
    fresh_ledger,
) -> None:
    decision_id = await _seed_decision(fresh_ledger)
    hub = _RecordingHub()
    with patch("notifications.get_hub", return_value=hub):
        await handle_ratify(_ctx(fresh_ledger), decision_id, signer="jin", note="ok")
    assert len(hub.events) == 1
    assert hub.events[0].event_type == "decision_ratified"


@pytest.mark.asyncio
async def test_reject_action_does_not_call_notify(fresh_ledger) -> None:
    """Phase 2a wires only ratify; reject is reserved for Phase 2b."""
    decision_id = await _seed_decision(fresh_ledger)
    hub = _RecordingHub()
    with patch("notifications.get_hub", return_value=hub):
        await handle_ratify(
            _ctx(fresh_ledger), decision_id, signer="jin", note="no", action="reject"
        )
    assert hub.events == []


# ── fail-isolation at the handler boundary ──────────────────────────


@pytest.mark.asyncio
async def test_ratify_returns_normally_when_notify_raises(fresh_ledger, caplog) -> None:
    """Handler-side try/except catches hub failures — ratify returns
    its normal response shape and logs a warning."""
    decision_id = await _seed_decision(fresh_ledger)
    hub = _RecordingHub(raise_on_notify=RuntimeError("hub blew up"))
    with patch("notifications.get_hub", return_value=hub):
        resp = await handle_ratify(_ctx(fresh_ledger), decision_id, signer="jin", note="ok")
    assert isinstance(resp, RatifyResponse)
    assert resp.was_new is True
    assert resp.signoff["state"] == "ratified"


# ── event payload carries the right metadata ─────────────────────────


@pytest.mark.asyncio
async def test_ratify_notification_event_carries_decision_id_and_signer_metadata(
    fresh_ledger,
) -> None:
    decision_id = await _seed_decision(fresh_ledger)
    hub = _RecordingHub()
    with patch("notifications.get_hub", return_value=hub):
        await handle_ratify(_ctx(fresh_ledger), decision_id, signer="jin", note="approved")
    event = hub.events[0]
    assert event.decision_id == decision_id
    assert event.summary == "approved"
    assert event.severity == "info"
    assert event.source_ref == "sha:deadbeef"


@pytest.mark.asyncio
async def test_ratify_notification_event_carries_no_raw_pii(fresh_ledger) -> None:
    """Reinforcement at the call site: the constructed event must not
    leak any PII fields. (Phase 1's dataclass shape already pins this
    at the contract layer; this re-asserts at the call site.)"""
    decision_id = await _seed_decision(fresh_ledger)
    hub = _RecordingHub()
    with patch("notifications.get_hub", return_value=hub):
        await handle_ratify(_ctx(fresh_ledger), decision_id, signer="jin", note="ok")
    event = hub.events[0]
    # The event is a frozen dataclass; we re-verify field names.
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(event)}
    forbidden = {"text", "description", "rationale", "speakers", "raw_content"}
    assert field_names.isdisjoint(forbidden)


# ── feature_area is empty-by-design in Phase 2a (audit advisory) ──────


@pytest.mark.asyncio
async def test_ratify_notification_feature_area_is_empty_in_phase_2a(
    fresh_ledger,
) -> None:
    """Phase 2a wires feature_area='' (no BicameralContext.feature_area
    field). Phase 2b owns decision-row resolution. This test pins the
    honest empty-default so a future change to populate it is a
    deliberate decision, not a silent fix."""
    decision_id = await _seed_decision(fresh_ledger)
    hub = _RecordingHub()
    with patch("notifications.get_hub", return_value=hub):
        await handle_ratify(_ctx(fresh_ledger), decision_id, signer="jin")
    assert hub.events[0].feature_area == ""
