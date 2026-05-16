"""End-to-end CLI tests for `bicameral-mcp diagnose` (#252 Layer 3 Phase 2).

Tests invoke ``cli.diagnose.main()`` directly (not via subprocess) to
avoid the subprocess overhead and the live network fetch for the
recommended-version heuristic. The recommended-version fetch is
monkeypatched to return None.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _patch_network(monkeypatch):
    """Avoid live HTTP fetch in tests."""
    from handlers import update as update_mod

    monkeypatch.setattr(update_mod, "_fetch_recommended_version", lambda channel="stable": None)
    monkeypatch.setattr(update_mod, "fetch_recommended_version", lambda channel="stable": None)
    yield


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Redirect home-dir reads to a clean tmp dir so preflight_events.jsonl is absent."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    yield


def test_diagnose_main_returns_zero_on_fresh_memory_ledger(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    from cli.diagnose import main

    rc = main()
    captured = capsys.readouterr()
    assert rc == 0
    assert "## Versions" in captured.out
    assert "## Schema revision sentinel" in captured.out


def test_diagnose_main_emits_all_required_sections(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    from cli.diagnose import main

    main()
    out = capsys.readouterr().out
    for header in (
        "## Versions",
        "## Ledger",
        "## Schema revision sentinel",
        "## Table row counts",
        "## Recent events",
        "## Suggested remediation",
    ):
        assert header in out


def test_diagnose_main_returns_one_on_adapter_connect_failure(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "ws://invalid-host-no-such-server:99999")

    # Force a connect failure by patching the adapter to raise on connect.
    from ledger import adapter as adapter_mod

    class _BoomAdapter(adapter_mod.SurrealDBLedgerAdapter):
        async def connect(self):
            raise RuntimeError("synthetic connect failure")

    monkeypatch.setattr(adapter_mod, "SurrealDBLedgerAdapter", _BoomAdapter)

    from cli.diagnose import main

    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "adapter connect failed" in captured.out
    assert "synthetic connect failure" in captured.out
