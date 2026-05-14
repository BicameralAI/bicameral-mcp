"""Functional tests for the `bicameral-mcp ledger-import` CLI (#252 Layer 4 Phase 2)."""

from __future__ import annotations

import io
import json

import pytest


def _write_fixture(path, lines):
    path.write_text("\n".join(lines), encoding="utf-8")


def test_import_cli_reads_from_file_argument(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    fixture = tmp_path / "fixture.jsonl"
    # Build a minimal valid source: bicameral_meta + schema_meta only
    # (Path B will DELETE+CREATE these; non-meta tables stay empty).
    lines = [
        json.dumps(
            {
                "_table": "bicameral_meta",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "bicameral_meta:fixture",
                "surrealdb_client_version_at_first_write": "fixture",
                "surrealdb_client_version_at_last_write": "fixture",
            }
        ),
        json.dumps(
            {
                "_table": "schema_meta",
                "_schema_version": 16,
                "_record_version": 1,
                "id": "schema_meta:fixture",
                "version": 16,
            }
        ),
    ]
    _write_fixture(fixture, lines)

    from cli.ledger_import_cli import main

    rc = main(from_file=str(fixture))
    assert rc == 0
    out = capsys.readouterr().out
    assert "ledger-import: wrote" in out
    assert "2 records" in out


def test_import_cli_reads_from_stdin_when_no_file(monkeypatch, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    payload = "\n".join(
        [
            json.dumps(
                {
                    "_table": "bicameral_meta",
                    "_schema_version": 16,
                    "_record_version": 1,
                    "id": "bicameral_meta:stdin_fixture",
                    "surrealdb_client_version_at_first_write": "stdin",
                    "surrealdb_client_version_at_last_write": "stdin",
                }
            ),
            json.dumps(
                {
                    "_table": "schema_meta",
                    "_schema_version": 16,
                    "_record_version": 1,
                    "id": "schema_meta:stdin_fixture",
                    "version": 16,
                }
            ),
        ]
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    from cli.ledger_import_cli import main

    rc = main()
    assert rc == 0


def test_import_cli_returns_one_on_validation_failure(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")
    fixture = tmp_path / "bad.jsonl"
    fixture.write_text(
        json.dumps(
            {"_table": "evil_unknown", "_schema_version": 16, "_record_version": 1, "id": "x:1"}
        ),
        encoding="utf-8",
    )

    from cli.ledger_import_cli import main

    rc = main(from_file=str(fixture))
    assert rc == 1
    err = capsys.readouterr().err
    assert "ledger-import" in err
    assert "evil_unknown" in err or "unknown _table" in err


def test_import_cli_returns_one_on_unreadable_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SURREAL_URL", "memory://")

    from cli.ledger_import_cli import main

    rc = main(from_file=str(tmp_path / "nonexistent.jsonl"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot read" in err
