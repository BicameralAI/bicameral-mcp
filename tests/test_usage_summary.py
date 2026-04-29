"""Tests for handlers/usage_summary.py (issue #42)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from handlers.usage_summary import handle_usage_summary


def _ctx_with_decisions(
    rows: list[dict] | None = None, cc_rows: list[dict] | None = None
) -> SimpleNamespace:
    """Build a fake ctx whose ledger.client.query returns staged rows."""
    client = MagicMock()
    call_count = {"i": 0}

    async def _query(sql: str, *args, **kwargs):
        call_count["i"] += 1
        if "FROM decision" in sql:
            return rows or []
        if "FROM compliance_check" in sql:
            return cc_rows or []
        return []

    client.query = _query
    inner = SimpleNamespace(_client=client)
    ledger = SimpleNamespace(_inner=inner)
    return SimpleNamespace(ledger=ledger)


@pytest.mark.asyncio
async def test_zero_days_returns_zeros(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """days=0 short-circuits the ledger query and returns base zeros + counter reads."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ctx = _ctx_with_decisions()
    out = await handle_usage_summary(ctx, days=0)
    assert out["period_days"] == 0
    assert out["decisions_ingested"] == 0
    assert out["reflected_pct"] == 0.0
    assert out["drift_pct"] == 0.0


@pytest.mark.asyncio
async def test_aggregate_decision_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    rows = [
        {"status": "reflected", "n": 8},
        {"status": "drifted", "n": 2},
        {"status": "ungrounded", "n": 5},
        {"status": "pending", "n": 3},
    ]
    ctx = _ctx_with_decisions(rows=rows, cc_rows=[])
    out = await handle_usage_summary(ctx, days=7)
    assert out["decisions_reflected"] == 8
    assert out["decisions_drifted"] == 2
    assert out["decisions_ungrounded"] == 5
    assert out["decisions_pending"] == 3
    assert out["decisions_ingested"] == 18
    assert out["reflected_pct"] == 0.8
    assert out["drift_pct"] == 0.2
    # reflected_pct + drift_pct ≤ 1.0 (acceptance criterion)
    assert out["reflected_pct"] + out["drift_pct"] <= 1.0


@pytest.mark.asyncio
async def test_cosmetic_drift_pct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cc = [
        {"verdict": "drifted", "n": 4},
        {"verdict": "cosmetic_autopass", "n": 6},
    ]
    ctx = _ctx_with_decisions(rows=[], cc_rows=cc)
    out = await handle_usage_summary(ctx, days=7)
    assert out["cosmetic_drift_pct"] == 0.6
    # Acceptance: between 0.0 and 1.0
    assert 0.0 <= out["cosmetic_drift_pct"] <= 1.0


@pytest.mark.asyncio
async def test_empty_ledger_no_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty tool_events / decision tables: numeric fields are 0.0, no error."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ctx = _ctx_with_decisions(rows=[], cc_rows=[])
    out = await handle_usage_summary(ctx, days=7)
    assert out["decisions_ingested"] == 0
    assert out["reflected_pct"] == 0.0
    assert out["drift_pct"] == 0.0
    assert out["cosmetic_drift_pct"] == 0.0


@pytest.mark.asyncio
async def test_tool_call_counts_from_local_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ingest_calls and bind_calls_total come from the local counters file."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib

    import local_counters

    importlib.reload(local_counters)
    for _ in range(3):
        local_counters.increment("bicameral-ingest")
    for _ in range(2):
        local_counters.increment("bicameral-bind")

    ctx = _ctx_with_decisions(rows=[], cc_rows=[])
    out = await handle_usage_summary(ctx, days=7)
    assert out["ingest_calls"] == 3
    assert out["bind_calls_total"] == 2
