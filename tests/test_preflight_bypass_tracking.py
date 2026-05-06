"""Behavioral tests for `handlers.record_bypass.handle_record_bypass`
respecting the `preflight_bypass_tracking` config gate (#200 Phase 3).

When `preflight_bypass_tracking="disabled"` in `.bicameral/config.yaml`,
the handler must short-circuit BEFORE the JSONL write to
`~/.bicameral/preflight_events.jsonl` and return
``recorded=False, reason="tracking_disabled"``. When `"enabled"`
(default), behavior is unchanged from the pre-#200 implementation.

The config gate is a deterministic server-side enforcement of the
operator's privacy choice; skill text references the config field
but does not implement the gate.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from handlers.record_bypass import handle_record_bypass


@dataclass
class _StubCtx:
    preflight_bypass_tracking: str = "enabled"


@pytest.mark.asyncio
async def test_record_bypass_no_op_when_disabled(tmp_path, monkeypatch) -> None:
    """preflight_bypass_tracking=disabled → handler returns
    recorded=False, reason='tracking_disabled', and does not invoke the
    JSONL writer at all (verified by monkeypatching write_bypass_event
    to raise — if it's called, the test fails)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    import preflight_telemetry

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("write_bypass_event should not be called when tracking is disabled")

    monkeypatch.setattr(preflight_telemetry, "write_bypass_event", _fail_if_called)

    ctx = _StubCtx(preflight_bypass_tracking="disabled")
    result = await handle_record_bypass(ctx, decision_id="d-test-1")

    assert result.recorded is False
    assert result.reason == "tracking_disabled"


@pytest.mark.asyncio
async def test_record_bypass_writes_event_when_enabled(tmp_path, monkeypatch) -> None:
    """preflight_bypass_tracking=enabled → handler invokes write_bypass_event
    (provided BICAMERAL_PREFLIGHT_TELEMETRY isn't disabled). Confirms the
    config gate doesn't interfere with the existing telemetry-enabled path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")

    import preflight_telemetry

    calls: list[tuple] = []

    def _capture(decision_id, reason="user_bypassed", state_preserved="proposed"):
        calls.append((decision_id, reason, state_preserved))

    monkeypatch.setattr(preflight_telemetry, "telemetry_enabled", lambda: True)
    monkeypatch.setattr(preflight_telemetry, "recent_bypass_seconds", lambda _: None)
    monkeypatch.setattr(preflight_telemetry, "write_bypass_event", _capture)

    ctx = _StubCtx(preflight_bypass_tracking="enabled")
    result = await handle_record_bypass(ctx, decision_id="d-test-2")

    assert result.recorded is True
    assert len(calls) == 1
    assert calls[0][0] == "d-test-2"
