"""Tests for the local preflight telemetry capture loop (#65, pieces 1-4).

Each test reroutes ``Path.home()`` to a per-test ``tmp_path`` so the salt
file, events file, and engagements file are isolated. We also reload the
``preflight_telemetry`` module each time so its module-level path constants
pick up the new home.
"""

from __future__ import annotations

import importlib
import json
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest


def _reload_pt(monkeypatch, home: Path):
    """Point HOME at ``home`` and reload preflight_telemetry so its module-level
    Path.home()-derived constants pick up the override."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows
    # Also patch Path.home directly because some envs ignore HOME on Windows.
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


# ── Phase 1: salt + hash helpers ────────────────────────────────────


def test_salt_persisted_to_user_home(pt, tmp_path):
    salt = pt._get_or_create_salt()
    assert isinstance(salt, bytes)
    assert len(salt) == 32
    salt_file = tmp_path / ".bicameral" / "salt"
    assert salt_file.exists()
    # Re-reading returns the same bytes.
    assert pt._get_or_create_salt() == salt
    assert salt_file.read_bytes() == salt


def test_salt_race_loser_reads_winner_bytes(pt, tmp_path, monkeypatch):
    """MF1: when O_EXCL fails because another process won, we read the file."""
    salt_file = tmp_path / ".bicameral" / "salt"
    salt_file.parent.mkdir(parents=True, exist_ok=True)
    # Pre-create the file as the "winner" would.
    winner_bytes = b"W" * 32
    salt_file.write_bytes(winner_bytes)
    # The exists() short-circuit means we read the winner directly.
    assert pt._get_or_create_salt() == winner_bytes


def test_salt_race_loser_handles_exclusive_failure(pt, tmp_path, monkeypatch):
    """MF1 explicit path: simulate the race where exists() was False but
    open() raised FileExistsError because the winner wrote in between."""
    salt_file = tmp_path / ".bicameral" / "salt"

    real_open = os.open
    winner_bytes = b"X" * 32

    def flaky_open(path, flags, mode=0o777):
        if str(salt_file) == str(path) and (flags & os.O_EXCL):
            # Simulate winner writing the file just before we try to create it.
            salt_file.parent.mkdir(parents=True, exist_ok=True)
            salt_file.write_bytes(winner_bytes)
            raise FileExistsError(17, "winner already wrote")
        return real_open(path, flags, mode)

    # Pre-condition: file doesn't exist yet so we go down the create path.
    if salt_file.exists():
        salt_file.unlink()
    monkeypatch.setattr(os, "open", flaky_open)
    result = pt._get_or_create_salt()
    assert result == winner_bytes


def test_hash_topic_stable_across_calls(pt):
    h1 = pt.hash_topic("Stripe webhook")
    h2 = pt.hash_topic("Stripe webhook")
    assert h1 == h2
    assert len(h1) == 16


def test_hash_topic_unstable_across_salts(pt, monkeypatch, tmp_path):
    h1 = pt.hash_topic("Stripe webhook")
    # Wipe the salt and reload to force a new salt.
    salt_file = tmp_path / ".bicameral" / "salt"
    salt_file.unlink()
    importlib.reload(pt)
    h2 = pt.hash_topic("Stripe webhook")
    assert h1 != h2


def test_hash_file_paths_order_independent(pt):
    h1 = pt.hash_file_paths(["a.py", "b.py"])
    h2 = pt.hash_file_paths(["b.py", "a.py"])
    assert h1 == h2


def test_hash_file_paths_skips_empty(pt):
    h1 = pt.hash_file_paths(["a.py", "b.py"])
    h2 = pt.hash_file_paths(["a.py", "", "  ", "b.py"])
    assert h1 == h2


def test_telemetry_disabled_by_default(pt_disabled, tmp_path):
    assert pt_disabled.telemetry_enabled() is False
    pt_disabled.write_preflight_event(
        session_id="s",
        preflight_id="p",
        topic="t",
        file_paths=["a.py"],
        fired=True,
        surfaced_ids=["d1"],
        reason="fired",
    )
    pt_disabled.write_engagement(
        session_id="s",
        tool="bicameral.bind",
        decision_id="d1",
        preflight_id="p",
        file_paths=["a.py"],
    )
    assert not (tmp_path / ".bicameral" / "preflight_events.jsonl").exists()
    assert not (tmp_path / ".bicameral" / "engagements.jsonl").exists()


def test_new_preflight_id_uuid4(pt):
    import uuid as _uuid

    pid = pt.new_preflight_id()
    assert _uuid.UUID(pid).version == 4
    # Two calls produce different ids
    assert pt.new_preflight_id() != pid


# ── Phase 3: event + engagement writers ─────────────────────────────


def test_write_preflight_event_appends_jsonl_with_hashed_topic(pt, tmp_path):
    pt.write_preflight_event(
        session_id="sess1",
        preflight_id="pid-1",
        topic="Stripe webhook",
        file_paths=["routes/webhook.py"],
        fired=True,
        surfaced_ids=["dec_1"],
        reason="fired",
    )
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    assert events_file.exists()
    rows = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["preflight_id"] == "pid-1"
    assert row["session_id"] == "sess1"
    assert row["fired"] is True
    assert row["surfaced_ids"] == ["dec_1"]
    assert "topic" not in row  # raw mode off
    assert len(row["topic_hash"]) == 16
    assert len(row["file_paths_hash"]) == 16


def test_write_preflight_event_no_op_when_disabled(pt_disabled, tmp_path):
    pt_disabled.write_preflight_event(
        session_id="s",
        preflight_id="p",
        topic="t",
        file_paths=[],
        fired=False,
        surfaced_ids=[],
        reason="no_matches",
    )
    assert not (tmp_path / ".bicameral" / "preflight_events.jsonl").exists()


def test_raw_capture_writes_topic_when_flag_set(monkeypatch, tmp_path):
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", "1")
    pt = _reload_pt(monkeypatch, tmp_path)
    pt.write_preflight_event(
        session_id="s",
        preflight_id="p",
        topic="Stripe webhook",
        file_paths=["a.py", "b.py"],
        fired=True,
        surfaced_ids=[],
        reason="fired",
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / ".bicameral" / "preflight_events.jsonl").read_text().splitlines()
    ]
    assert rows[0]["topic"] == "Stripe webhook"
    assert rows[0]["file_paths"] == ["a.py", "b.py"]
    # Hashed columns still present
    assert len(rows[0]["topic_hash"]) == 16


def test_write_engagement_appends_with_preflight_id_attribution(pt, tmp_path):
    pt.write_engagement(
        session_id="sess1",
        tool="bicameral.bind",
        decision_id="dec_1",
        preflight_id="pid-1",
        file_paths=["a.py"],
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / ".bicameral" / "engagements.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["preflight_id"] == "pid-1"
    assert rows[0]["attribution"] == "explicit"
    assert rows[0]["tool"] == "bicameral.bind"
    assert rows[0]["decision_id"] == "dec_1"


def test_engagement_fallback_attribution_via_subset_match(pt, tmp_path):
    # Prime an event with a known file_paths set.
    pt.write_preflight_event(
        session_id="s",
        preflight_id="parent-pid",
        topic="checkout flow",
        file_paths=["checkout.py", "billing.py"],
        fired=True,
        surfaced_ids=["d1"],
        reason="fired",
    )
    # Engage without an explicit preflight_id but matching paths.
    pt.write_engagement(
        session_id="s",
        tool="bicameral.bind",
        decision_id="d1",
        preflight_id=None,
        file_paths=["checkout.py", "billing.py"],
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / ".bicameral" / "engagements.jsonl").read_text().splitlines()
    ]
    assert rows[0]["attribution"] == "fallback"
    assert rows[0]["preflight_id"] == "parent-pid"


def test_engagement_fallback_no_match_leaves_preflight_id_none(pt, tmp_path):
    pt.write_engagement(
        session_id="s",
        tool="bicameral.bind",
        decision_id=None,
        preflight_id=None,
        file_paths=["unrelated.py"],
    )
    rows = [
        json.loads(line)
        for line in (tmp_path / ".bicameral" / "engagements.jsonl").read_text().splitlines()
    ]
    assert rows[0]["attribution"] == "fallback"
    assert rows[0]["preflight_id"] is None


# ── Phase 4: retention rotation ─────────────────────────────────────


def test_rotation_at_50mb(pt, tmp_path, monkeypatch):
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    # Write a big file directly (don't actually loop millions of writes).
    events_file.write_bytes(b"x" * (51 * 10**6))
    pt.write_preflight_event(
        session_id="s",
        preflight_id="p",
        topic="t",
        file_paths=[],
        fired=True,
        surfaced_ids=[],
        reason="fired",
    )
    # The original was rotated to .1, and a new active file was created with
    # exactly the latest record.
    rotated = events_file.with_suffix(events_file.suffix + ".1")
    assert rotated.exists()
    # Active file got the new (small) record.
    active_text = events_file.read_text()
    assert "preflight_id" in active_text
    assert len(active_text.splitlines()) == 1


def test_rotation_at_30_days(pt, tmp_path):
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.write_text('{"old":"row"}\n')
    # Backdate mtime by 31 days.
    old_ts = (datetime.now(UTC) - timedelta(days=31)).timestamp()
    os.utime(events_file, (old_ts, old_ts))
    pt.write_preflight_event(
        session_id="s",
        preflight_id="p",
        topic="t",
        file_paths=[],
        fired=False,
        surfaced_ids=[],
        reason="no_matches",
    )
    rotated = events_file.with_suffix(events_file.suffix + ".1")
    assert rotated.exists()
    assert '{"old":"row"}' in rotated.read_text()


def test_rotated_files_keep_last_n(pt, tmp_path):
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    # Pre-populate .1 .. .5 to simulate 5 prior rotations.
    for i in range(1, 6):
        rot = events_file.with_suffix(events_file.suffix + f".{i}")
        rot.write_text(f"rotation-{i}\n")
    # Now seed an oversized active file and trigger rotation.
    events_file.write_bytes(b"y" * (51 * 10**6))
    pt._maybe_rotate(events_file)
    # .1 should now hold what was the active file (binary 'y' content),
    # .2..5 should have shifted, and the original .5 should be dropped.
    assert events_file.with_suffix(events_file.suffix + ".5").exists()
    # Content of .5 should be the old .4 (i.e. "rotation-4")
    assert "rotation-4" in events_file.with_suffix(events_file.suffix + ".5").read_text()
    # The originally-newest rotation (1) was bumped to .2.
    assert "rotation-1" in events_file.with_suffix(events_file.suffix + ".2").read_text()
    # No .6 exists.
    assert not events_file.with_suffix(events_file.suffix + ".6").exists()


def test_rotation_no_op_when_under_threshold(pt, tmp_path):
    events_file = tmp_path / ".bicameral" / "preflight_events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    events_file.write_text('{"a":1}\n')
    pt._maybe_rotate(events_file)
    assert events_file.exists()
    assert not events_file.with_suffix(events_file.suffix + ".1").exists()
