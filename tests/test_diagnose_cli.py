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


def test_diagnose_main_returns_one_on_raw_client_connect_failure(monkeypatch, capsys):
    """#410: CLI diagnose now uses a raw LedgerClient (no init_schema/migrate),
    so the failure surface is ``LedgerClient.connect``, not the adapter. The
    error envelope must still surface a recovery path that the agent can act
    on without an MCP session."""
    monkeypatch.setenv("SURREAL_URL", "ws://invalid-host-no-such-server:99999")

    from ledger import client as client_mod

    async def _boom(self):
        raise RuntimeError("synthetic connect failure")

    monkeypatch.setattr(client_mod.LedgerClient, "connect", _boom)

    from cli.diagnose import main

    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "raw client connect failed" in captured.out
    assert "synthetic connect failure" in captured.out
    # Recovery hint must surface the now-callable CLI form (#410).
    assert "bicameral-mcp reset --confirm" in captured.out


def test_diagnose_main_cli_does_not_use_adapter_path(monkeypatch, capsys):
    """#410 regression: the CLI must NOT route through SurrealDBLedgerAdapter
    (which runs init_schema/migrate). If the adapter were used here, a
    corrupted DefineTableStatement on disk would crash the bug-report tool
    in exactly the moment it's needed.

    We assert this structurally: patch the adapter's connect to blow up and
    confirm diagnose still returns 0 against a fresh memory:// ledger.
    """
    monkeypatch.setenv("SURREAL_URL", "memory://")

    from ledger import adapter as adapter_mod

    async def _adapter_should_not_be_called(self):
        raise AssertionError(
            "cli.diagnose must not use SurrealDBLedgerAdapter — it should "
            "open a raw LedgerClient that skips init_schema/migrate (#410)."
        )

    monkeypatch.setattr(
        adapter_mod.SurrealDBLedgerAdapter, "connect", _adapter_should_not_be_called
    )

    from cli.diagnose import main

    rc = main()
    captured = capsys.readouterr()
    assert rc == 0, captured.out
    assert "## Versions" in captured.out
