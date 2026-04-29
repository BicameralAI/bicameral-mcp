"""Unit tests for local_counters.py (issue #39)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


def _counters_path(home: Path) -> Path:
    return home / ".bicameral" / "counters.jsonl"


def test_increment_creates_counter_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib
    import local_counters
    importlib.reload(local_counters)

    local_counters.increment("bicameral-ingest")

    p = _counters_path(tmp_path)
    assert p.exists()
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_increment_appends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib
    import local_counters
    importlib.reload(local_counters)

    for _ in range(50):
        local_counters.increment("bicameral-ingest")
    lines = _counters_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50


def test_read_counters_aggregates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib
    import local_counters
    importlib.reload(local_counters)

    for _ in range(3):
        local_counters.increment("bicameral-ingest")
    for _ in range(7):
        local_counters.increment("bicameral-bind")

    counts = local_counters.read_counters()
    assert counts == {"bicameral-ingest": 3, "bicameral-bind": 7}


def test_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch urlopen to raise; increment must still succeed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib
    import local_counters
    importlib.reload(local_counters)

    with patch("urllib.request.urlopen", side_effect=RuntimeError("net down")):
        local_counters.increment("bicameral-ingest")
    assert _counters_path(tmp_path).exists()


def test_concurrent_increments_no_data_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib
    import local_counters
    importlib.reload(local_counters)

    def _worker(idx: int) -> None:
        for _ in range(50):
            local_counters.increment(f"tool-{idx % 4}")

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = local_counters.read_counters()
    assert sum(counts.values()) == 200


def test_disabled_when_env_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("BICAMERAL_LOCAL_COUNTERS", "0")
    import importlib
    import local_counters
    importlib.reload(local_counters)

    local_counters.increment("bicameral-ingest")
    assert not _counters_path(tmp_path).exists()


def test_read_counters_handles_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    import importlib
    import local_counters
    importlib.reload(local_counters)

    assert local_counters.read_counters() == {}
