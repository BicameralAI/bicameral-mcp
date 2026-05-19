"""Non-interactive ``bicameral-mcp reset`` CLI (#410).

Tests the new flag surface (``--confirm``, ``--wipe-mode``,
``--replay-from-events``) and the filesystem-only fallback that fires
when the ledger can't connect — the exact scenario that motivated the
issue.

Sociable: builds a real ``BicameralContext.from_env()`` against
``memory://`` for the happy path. The fallback test uses a real
``surrealkv://`` directory on disk so the rmtree path is exercised
end-to-end.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _patch_network(monkeypatch):
    """Avoid live HTTP fetches during reset (the update-version probe)."""
    try:
        from handlers import update as update_mod

        monkeypatch.setattr(
            update_mod, "_fetch_recommended_version", lambda channel="stable": None
        )
        monkeypatch.setattr(update_mod, "fetch_recommended_version", lambda channel="stable": None)
    except ImportError:
        pass
    yield


def test_reset_cli_help_lists_noninteractive_flags():
    """The flag set is the agent's contract — guard against silent removal."""
    from argparse import ArgumentParser

    from server import _register_subparsers

    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    _register_subparsers(parser, subparsers)
    args = parser.parse_args(
        ["reset", "--confirm", "--wipe-mode", "full", "--replay-from-events"]
    )
    assert args.command == "reset"
    assert args.confirm is True
    assert args.wipe_mode == "full"
    assert args.replay_from_events is True


def test_reset_cli_ledger_wipe_against_memory_ledger(monkeypatch, capsys):
    """Happy path: --confirm --wipe-mode=ledger against memory:// succeeds
    end-to-end and emits a parseable JSON ResetResponse."""
    monkeypatch.setenv("SURREAL_URL", "memory://")
    monkeypatch.setenv("REPO_PATH", str(monkeypatch.__class__.__name__))  # any string repo id

    from cli.reset_cli import run_noninteractive_reset

    rc = run_noninteractive_reset(wipe_mode="ledger", replay_from_events=False)
    out = capsys.readouterr().out
    assert rc == 0, out
    payload = json.loads(out)
    assert payload["wiped"] is True
    assert payload["wipe_mode"] == "ledger"


def test_reset_cli_full_wipe_falls_back_to_filesystem_when_connect_fails(
    monkeypatch, capsys, tmp_path
):
    """#410 core: when BicameralContext.from_env() raises (e.g. corrupted DB
    that the SDK can't deserialize), --wipe-mode=full must still recover via
    direct ``shutil.rmtree`` of the resolved .bicameral/ dir."""
    bicameral_dir = tmp_path / ".bicameral"
    db_dir = bicameral_dir / "ledger.db"
    db_dir.mkdir(parents=True)
    (db_dir / "corrupted.kv").write_bytes(b"\x00\x01\x02")
    (bicameral_dir / "config.yaml").write_text("# stale\n", encoding="utf-8")
    ledger_url = f"surrealkv://{db_dir}"
    monkeypatch.setenv("SURREAL_URL", ledger_url)

    # Force the high-level path to fail — simulates the on-disk corruption
    # without us having to write a malformed surrealkv archive.
    import context as context_mod

    def _boom_from_env(cls):
        raise RuntimeError(
            "SurrealDB row deserialization failed: Invalid revision `101` for "
            "type `DefineTableStatement`"
        )

    monkeypatch.setattr(context_mod.BicameralContext, "from_env", classmethod(_boom_from_env))

    from cli.reset_cli import run_noninteractive_reset

    rc = run_noninteractive_reset(wipe_mode="full", replay_from_events=False)
    out = capsys.readouterr().out
    assert rc == 0, out
    payload = json.loads(out)
    assert payload["wiped"] is True
    assert payload["fallback"] == "filesystem_only"
    assert payload["bicameral_dir"] == str(bicameral_dir)
    # The directory must actually be gone — recovery is observable, not advisory.
    assert not bicameral_dir.exists()


def test_reset_cli_ledger_mode_does_not_silently_fallback_when_connect_fails(
    monkeypatch, capsys, tmp_path
):
    """Ledger-mode wipe needs a working connection (we delete by SurrealQL,
    not rmtree). If from_env() raises, we must NOT silently rmtree —
    that would surprise the operator. Return 1 with a clear error and
    point them at --wipe-mode=full."""
    bicameral_dir = tmp_path / ".bicameral"
    (bicameral_dir / "ledger.db").mkdir(parents=True)
    monkeypatch.setenv("SURREAL_URL", f"surrealkv://{bicameral_dir / 'ledger.db'}")

    import context as context_mod

    def _boom_from_env(cls):
        raise RuntimeError("synthetic connect failure")

    monkeypatch.setattr(context_mod.BicameralContext, "from_env", classmethod(_boom_from_env))

    from cli.reset_cli import run_noninteractive_reset

    rc = run_noninteractive_reset(wipe_mode="ledger", replay_from_events=False)
    captured = capsys.readouterr()
    assert rc == 1
    assert "synthetic connect failure" in captured.err
    assert "--wipe-mode=full" in captured.err
    # Side-effect-free: the directory must still be on disk.
    assert bicameral_dir.exists()


def test_recovery_hint_in_LedgerDeserializationError_mentions_cli_form():
    """The error message agents see must surface the shell escape hatch (#410)."""
    from ledger.client import LedgerDeserializationError

    hint = LedgerDeserializationError.RECOVERY_HINT
    assert "bicameral-mcp reset --confirm" in hint
    # MCP form should still be present as the alternative.
    assert "bicameral_reset(" in hint


def test_diagnose_classify_recovery_row_warning_surfaces_cli_form():
    """The diagnose handler's next_action must include the CLI form when
    row-warnings classify as reset_rebuild / reset_destructive."""
    from handlers.diagnose import _classify_recovery

    class _D:
        schema_version_recorded = 25
        schema_version_expected = 25
        ledger_url = "surrealkv:///nonexistent/ledger.db"
        row_probe_warnings = ["ledger_sync: LedgerDeserializationError: Invalid revision `0`"]
        table_counts = {"decision": 5}

    path, next_action = _classify_recovery(_D())
    assert path in ("reset_rebuild", "reset_destructive")
    assert "bicameral-mcp reset --confirm" in next_action
