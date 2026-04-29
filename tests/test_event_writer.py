"""Cross-platform regression tests for ``events.writer`` (issue #74).

Issue #74: ``import fcntl`` was at module top-level, which is Unix-only
and broke ALL ingest-using tests on Windows at import time.

These tests verify:

1. ``events.writer`` imports cleanly on the current platform.
2. ``EventFileWriter.write()`` produces a well-formed JSONL line and
   can be invoked twice in succession (i.e. the lock is taken and
   released correctly — a leaked lock would deadlock the second call).
3. The platform-conditional lock helpers exist and dispatch correctly.

We don't test concurrent multi-process locking here — that's the
domain of an OS-level integration test. We just guarantee the
single-writer happy path works on every platform we support.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from events.writer import EventFileWriter, _lock_exclusive, _unlock


def test_writer_module_imports_cleanly() -> None:
    """Sanity: the module imports without raising on this platform.

    The original bug (#74) raised ``ModuleNotFoundError: No module
    named 'fcntl'`` at import time on Windows. Hitting any code path
    that pulled in ``events.writer`` collapsed the whole test session.
    """
    import events.writer  # noqa: F401 — import side-effect IS the test


def test_lock_helpers_exist_for_current_platform() -> None:
    """Sanity: the platform-dispatched helpers are callable."""
    assert callable(_lock_exclusive)
    assert callable(_unlock)


def test_write_produces_jsonl_line(tmp_path: Path) -> None:
    """A single write() yields a parseable JSONL line."""
    events_dir = tmp_path / "events"
    writer = EventFileWriter(events_dir, "test@example.com")

    path = writer.write("decision_recorded", {"decision_id": "decision:abc"})

    assert path == events_dir / "test@example.com.jsonl"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert content.endswith("\n"), "JSONL line must terminate with newline"
    line = content.rstrip("\n")
    parsed = json.loads(line)
    assert parsed["event_type"] == "decision_recorded"
    assert parsed["author"] == "test@example.com"
    assert parsed["payload"] == {"decision_id": "decision:abc"}


def test_consecutive_writes_release_lock(tmp_path: Path) -> None:
    """Two writes back-to-back must succeed — proves the lock is released.

    A leaked exclusive lock would deadlock the second ``open(... "ab")``
    + ``_lock_exclusive`` call, hanging the test until pytest's
    timeout. If this test passes quickly, the lock is being released.
    """
    events_dir = tmp_path / "events"
    writer = EventFileWriter(events_dir, "test@example.com")

    writer.write("event_one", {"n": 1})
    writer.write("event_two", {"n": 2})

    lines = (events_dir / "test@example.com.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event_type"] == "event_one"
    assert json.loads(lines[1])["event_type"] == "event_two"


def test_write_with_empty_file_locks_cleanly(tmp_path: Path) -> None:
    """Locking byte 0 on a previously-empty file must succeed.

    Windows-specific concern: ``msvcrt.locking`` operates on a byte
    range — an empty file has no bytes. We lock byte 0 anyway because
    the OS-level lock is a metadata marker, not a region read. Verify
    the first write to a fresh file works (file is created at 0 bytes,
    then we open + lock + write).
    """
    events_dir = tmp_path / "events"
    writer = EventFileWriter(events_dir, "fresh@example.com")
    target = events_dir / "fresh@example.com.jsonl"
    assert not target.exists(), "precondition: file should not exist yet"

    writer.write("first_event", {"hello": "world"})

    assert target.exists()
    line = target.read_text(encoding="utf-8").rstrip("\n")
    assert json.loads(line)["event_type"] == "first_event"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific dispatch")
def test_windows_uses_msvcrt() -> None:
    """On Windows, the lock helpers dispatch to msvcrt, not fcntl."""
    import events.writer as ew

    # If the module accidentally re-introduces a top-level ``fcntl``
    # import on Windows, this test still passes — but the very first
    # test (``test_writer_module_imports_cleanly``) would fail at
    # collection time. That covers the regression directly.
    assert "msvcrt" in sys.modules, "msvcrt should be loaded on Windows"
    # Spot-check the helpers are bound (not the POSIX versions).
    assert ew._lock_exclusive.__doc__ is not None
    assert "Windows" in ew._lock_exclusive.__doc__


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-specific dispatch")
def test_posix_uses_fcntl() -> None:
    """On POSIX, the lock helpers dispatch to fcntl."""
    import events.writer as ew

    assert "fcntl" in sys.modules, "fcntl should be loaded on POSIX"
    assert ew._lock_exclusive.__doc__ is not None
    assert "POSIX" in ew._lock_exclusive.__doc__
