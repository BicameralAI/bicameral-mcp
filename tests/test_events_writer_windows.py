"""Cross-platform import + write tests for events.writer.

The pre-fix code had a top-level ``import fcntl`` which is POSIX-only and
broke ALL ingest-using tests on Windows at collection time. This module
verifies the platform gate works and the JSONL append path stays atomic
across both POSIX and Windows.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from pathlib import Path

import pytest


def test_writer_imports_on_current_platform() -> None:
    """events.writer imports cleanly without ImportError on the current platform.

    Pre-fix this raised ImportError on Windows because of top-level
    ``import fcntl``. After fix, the import is platform-gated.
    """
    import events.writer
    importlib.reload(events.writer)
    assert hasattr(events.writer, "EventFileWriter")


def test_writer_imports_when_fcntl_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate non-POSIX platform by hiding fcntl from sys.modules."""
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setattr(sys, "platform", "win32")
    import events.writer
    importlib.reload(events.writer)
    assert events.writer.fcntl is None


def test_writer_appends_event(tmp_path: Path) -> None:
    """Single write produces one parseable JSONL line with the right fields."""
    from events.writer import EventEnvelope, EventFileWriter

    events_dir = tmp_path / "events"
    writer = EventFileWriter(events_dir, "alice@example.com")
    out_path = writer.write("decision_added", {"id": "d1"})

    assert out_path == events_dir / "alice@example.com.jsonl"
    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    parsed = EventEnvelope.model_validate(record)
    assert parsed.event_type == "decision_added"
    assert parsed.author == "alice@example.com"
    assert parsed.payload == {"id": "d1"}


def test_concurrent_writes_no_data_loss(tmp_path: Path) -> None:
    """Two threads writing concurrently produce two parseable lines.

    On POSIX this exercises the flock path; on Windows it exercises the
    no-flock path. Either way, the JSONL file must contain both events
    intact (no torn writes for short payloads).
    """
    from events.writer import EventFileWriter

    events_dir = tmp_path / "events"
    writer = EventFileWriter(events_dir, "alice@example.com")

    def _write(idx: int) -> None:
        for i in range(10):
            writer.write("evt", {"thread": idx, "i": i})

    threads = [threading.Thread(target=_write, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40
    for line in lines:
        record = json.loads(line)
        assert "event_type" in record
        assert "author" in record
        assert "timestamp" in record
        assert "payload" in record
