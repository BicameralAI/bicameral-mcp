"""Functional tests for the `bicameral-mcp ledger-export` CLI (#252 Layer 4 Phase 2)."""

from __future__ import annotations

import json

import pytest


def test_export_cli_returns_zero_on_fresh_memory_ledger(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    from cli.ledger_export_cli import main

    rc = main()
    assert rc == 0
    captured = capsys.readouterr()
    # Layer 2 sentinel writes bicameral_meta + schema_meta on connect; both
    # appear in the export output.
    assert captured.out.strip(), "expected non-empty output"


def test_export_cli_emits_valid_jsonl_per_line(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    from cli.ledger_export_cli import main

    main()
    out = capsys.readouterr().out
    for line in out.strip().splitlines():
        rec = json.loads(line)  # raises on invalid JSON
        assert "_table" in rec
        assert "_schema_version" in rec
        assert "_record_version" in rec


def test_export_cli_returns_one_on_adapter_failure(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    from ledger import adapter as adapter_mod

    class _BoomAdapter(adapter_mod.SurrealDBLedgerAdapter):
        async def connect(self):
            raise RuntimeError("synthetic connect failure")

    monkeypatch.setattr(adapter_mod, "SurrealDBLedgerAdapter", _BoomAdapter)
    from cli import ledger_export_cli

    monkeypatch.setattr(ledger_export_cli, "SurrealDBLedgerAdapter", _BoomAdapter, raising=False)
    # Re-import inside main() picks up the patched class via monkeypatch.
    from cli.ledger_export_cli import main

    rc = main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "ledger-export" in err
    assert "synthetic connect failure" in err
