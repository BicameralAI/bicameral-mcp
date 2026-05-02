"""Phase 4 (#112) — bicameral.record_bypass MCP handler tests.

Covers:
  - Fresh write returns recorded=True, deduped=False.
  - Telemetry disabled returns recorded=False, deduped=False,
    reason="telemetry_disabled".
  - Idempotency: second call inside the window returns
    recorded=False, deduped=True (V4 spam-bypass guard).
  - Missing/empty decision_id returns recorded=False, deduped=False,
    reason="invalid_decision_id".
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from handlers.record_bypass import handle_record_bypass


def _reload_pt(monkeypatch, home: Path):
    """Reload preflight_telemetry against an isolated home dir."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    import preflight_telemetry as pt

    importlib.reload(pt)
    return pt


@pytest.fixture
def pt(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    return _reload_pt(monkeypatch, tmp_path)


@pytest.fixture
def pt_disabled(monkeypatch, tmp_path):
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    return _reload_pt(monkeypatch, tmp_path)


class _StubCtx:
    """Handler ignores ctx (bypass storage is local JSONL, not ledger)."""


@pytest.mark.asyncio
async def test_fresh_bypass_returns_recorded_true(pt, tmp_path):
    """First call writes a row and returns recorded=True, deduped=False."""
    resp = await handle_record_bypass(_StubCtx(), decision_id="dec-fresh")
    assert resp.recorded is True
    assert resp.deduped is False
    assert resp.reason is None
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    assert events_file.exists()
    contents = events_file.read_text()
    assert "preflight_prompt_bypassed" in contents
    assert "dec-fresh" in contents


@pytest.mark.asyncio
async def test_telemetry_disabled_no_op(pt_disabled, tmp_path):
    """Telemetry off: handler returns the disabled sentinel, no write."""
    resp = await handle_record_bypass(_StubCtx(), decision_id="dec-x")
    assert resp.recorded is False
    assert resp.deduped is False
    assert resp.reason == "telemetry_disabled"
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    assert not events_file.exists()


@pytest.mark.asyncio
async def test_idempotent_within_window_returns_deduped_true(pt, tmp_path):
    """Second call inside the recency window returns deduped=True."""
    first = await handle_record_bypass(_StubCtx(), decision_id="dec-dup")
    assert first.recorded is True and first.deduped is False
    second = await handle_record_bypass(_StubCtx(), decision_id="dec-dup")
    assert second.recorded is False
    assert second.deduped is True
    assert second.reason is None
    # Only one row in the JSONL.
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    rows = [ln for ln in events_file.read_text().splitlines() if ln.strip()]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_missing_decision_id_returns_invalid_decision_id(pt):
    """Empty/None decision_id is rejected without writing."""
    resp = await handle_record_bypass(_StubCtx(), decision_id="")
    assert resp.recorded is False
    assert resp.deduped is False
    assert resp.reason == "invalid_decision_id"
