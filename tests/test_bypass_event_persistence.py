"""Phase 4 (#112) — bypass event JSONL persistence + engine integration.

Mirrors the per-test ``Path.home()`` reload pattern from
``test_preflight_telemetry.py`` so each test gets an isolated
``~/.bicameral/preflight_events.jsonl``. The engine integration test
exercises the actual JSONL-driven recency lookup that Phase 3
mocked.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def _reload_pt(monkeypatch, home: Path):
    """Point HOME at ``home`` and reload preflight_telemetry so its
    module-level Path.home()-derived constants pick up the override."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    import preflight_telemetry as pt

    importlib.reload(pt)
    return pt


@pytest.fixture
def pt(monkeypatch, tmp_path):
    """Fresh preflight_telemetry pointed at tmp_path, telemetry enabled."""
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", raising=False)
    return _reload_pt(monkeypatch, tmp_path)


@pytest.fixture
def pt_disabled(monkeypatch, tmp_path):
    """Fresh preflight_telemetry pointed at tmp_path, telemetry disabled."""
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", raising=False)
    return _reload_pt(monkeypatch, tmp_path)


# ── Persistence tests ────────────────────────────────────────────────


def test_bypass_event_appends_to_jsonl(pt, tmp_path):
    """write_bypass_event appends a preflight_prompt_bypassed line."""
    pt.write_bypass_event("dec-1", reason="user_bypassed", state_preserved="proposed")
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    assert events_file.exists()
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event_type"] == "preflight_prompt_bypassed"
    assert row["decision_id"] == "dec-1"
    assert row["reason"] == "user_bypassed"
    assert row["state_preserved"] == "proposed"
    assert row["risk_visible"] is True
    assert "ts" in row


def test_bypass_event_records_state_preserved(pt, tmp_path):
    """state_preserved is recorded verbatim for the audit trail."""
    pt.write_bypass_event("dec-2", state_preserved="collision_pending")
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert rows[0]["state_preserved"] == "collision_pending"


def test_bypass_event_no_op_when_telemetry_disabled(pt_disabled, tmp_path):
    """No write happens when BICAMERAL_PREFLIGHT_TELEMETRY=0."""
    pt_disabled.write_bypass_event("dec-x", reason="user_bypassed")
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    assert not events_file.exists()


def test_bypass_event_idempotent_within_window(pt, tmp_path):
    """V4 spam-bypass guard: second write inside the window is a no-op."""
    pt.write_bypass_event("dec-recent")
    pt.write_bypass_event("dec-recent")  # should be a no-op
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    assert len(rows) == 1, "second bypass within recency window must be deduped (V4 guard)"


def test_recent_bypass_seconds_ignores_events_older_than_window(pt, tmp_path):
    """An event older than the recency window does not block a new write."""
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (
        datetime.now(UTC) - timedelta(seconds=pt._BYPASS_RECENCY_WINDOW_SECONDS + 60)
    ).isoformat()
    events_file.write_text(
        json.dumps(
            {
                "ts": old_ts,
                "event_type": "preflight_prompt_bypassed",
                "decision_id": "dec-old",
                "reason": "user_bypassed",
                "state_preserved": "proposed",
                "risk_visible": True,
            }
        )
        + "\n"
    )
    # Out-of-window — recency lookup returns None.
    assert pt.recent_bypass_seconds("dec-old") is None
    # And a fresh write goes through (not deduped by the stale event).
    pt.write_bypass_event("dec-old")
    rows = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
    # Two rows: the stale one + the fresh write.
    assert len(rows) == 2


def test_engine_reads_recent_bypass_drops_tier(pt, tmp_path):
    """End-to-end: engine.evaluate sees the JSONL-driven recency and
    drops one tier on the action ladder.

    Phase 3 verified ``_apply_bypass_downgrade`` directly with mocked
    integers; Phase 4 wires the actual JSONL lookup. We write a fresh
    bypass for ``dec-eng-1``, look it up via ``recent_bypass_seconds``,
    pass the scalar into the engine, and assert the action drops one
    rung.
    """
    from governance import config as governance_config
    from governance import engine as governance_engine
    from governance.contracts import GovernanceFinding, GovernanceMetadata

    # Fresh bypass; recency should be < window.
    pt.write_bypass_event("dec-eng-1")
    recency = pt.recent_bypass_seconds("dec-eng-1")
    assert recency is not None
    assert recency < pt._BYPASS_RECENCY_WINDOW_SECONDS

    # Build a finding + metadata that would land on `escalate` under
    # default config + likely_drift severity. After bypass downgrade
    # we expect `warn` (one tier softer).
    finding = GovernanceFinding(
        finding_id="f-1",
        decision_id="dec-eng-1",
        region_id=None,
        decision_class="architecture",
        risk_class="medium",
        escalation_class="escalate",
        source="preflight",
        semantic_status="likely_drift",
        confidence={},
        explanation="test",
        evidence_refs=[],
    )
    metadata = GovernanceMetadata(
        decision_class="architecture",
        risk_class="medium",
        escalation_class="escalate",
    )
    cfg = governance_config.GovernanceConfig()

    # Without bypass: escalate (or higher pre-ceiling).
    no_bypass = governance_engine.evaluate(
        finding=finding,
        metadata=metadata,
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=None,
    )
    # With bypass: one tier softer.
    with_bypass = governance_engine.evaluate(
        finding=finding,
        metadata=metadata,
        config=cfg,
        decision_status="ratified",
        bypass_recency_seconds=recency,
    )
    ladder = governance_engine._ACTION_LADDER
    assert ladder.index(with_bypass.action) == max(0, ladder.index(no_bypass.action) - 1), (
        f"expected one-tier downgrade; baseline={no_bypass.action}, "
        f"after_bypass={with_bypass.action}"
    )
