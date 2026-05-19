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

        monkeypatch.setattr(update_mod, "_fetch_recommended_version", lambda channel="stable": None)
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
    args = parser.parse_args(["reset", "--confirm", "--wipe-mode", "full", "--replay-from-events"])
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


def test_resolve_events_dir_under_legacy_local_layout_finds_sibling_events(monkeypatch, tmp_path):
    """Regression for the silent-zero-replay bug (#410 follow-up):

    Pre-fix, ``_resolve_events_dir`` derived its target via
    ``Path(ledger.db).parent / events``. Under the layout where the
    ledger sits at ``<bicameral_dir>/local/ledger.db`` (BICAMERAL_DATA_PATH
    test harness, and pre-#368 production), that lands at
    ``<bicameral_dir>/local/events/`` — one directory too deep — and
    silently returns ``None`` while real events sit at the sibling
    ``<bicameral_dir>/events/``. Replay then short-circuits to 0 with
    no error surfaced.

    Post-fix the resolver routes forward (env override → repo-local
    path) and finds the canonical events dir regardless of where the
    user-local ledger file lives.
    """
    monkeypatch.setenv("BICAMERAL_DATA_PATH", str(tmp_path))

    events_dir = tmp_path / ".bicameral" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "test@example.com.jsonl").write_text(
        '{"event_type":"ingest.completed"}\n', encoding="utf-8"
    )

    from handlers.reset import _count_events_on_disk, _resolve_events_dir

    resolved = _resolve_events_dir(repo_path=None)
    assert resolved == events_dir, (
        f"resolver landed at {resolved!r}, expected sibling-of-local events dir "
        f"{events_dir!r} — the silent-zero-replay regression has returned."
    )
    assert _count_events_on_disk(repo_path=None) == 1


def test_resolve_events_dir_uses_repo_path_not_locator_project_dir(monkeypatch, tmp_path):
    """Events are repo-local (committed to git pre-#373, gdrive post-#373),
    NOT user-local state. The locator owns user-local paths only
    (ledger.db, code-graph, bm25, watermark, transcript queues,
    operator.yaml). Routing events through ``project_dir_for() / events``
    would silently break on fresh clones — events would resolve to an
    empty user-local cache instead of the in-repo source. Guard the
    boundary with a direct assertion.
    """
    monkeypatch.delenv("BICAMERAL_DATA_PATH", raising=False)

    repo = tmp_path / "fake-repo"
    events_dir = repo / ".bicameral" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "test@example.com.jsonl").write_text(
        '{"event_type":"ingest.completed"}\n', encoding="utf-8"
    )

    from handlers.reset import _resolve_events_dir

    resolved = _resolve_events_dir(repo_path=str(repo))
    assert resolved == events_dir, (
        f"events dir must be repo-relative (resolved={resolved!r}, "
        f"expected={events_dir!r}). If this routes through the locator's "
        f"project_dir_for(), the resolver has confused user-local state "
        f"with the repo-local event substrate — see #373."
    )


def test_reset_cli_replay_surfaces_missing_events_dir_as_replay_error(
    monkeypatch, capsys, tmp_path
):
    """Regression for the silent-zero-replay bug (#410 follow-up):

    When the events substrate can't be located, the response must NOT
    look successful with ``events_replayed: 0``. The handler must
    raise from ``_replay_events_into_ledger`` and the wrapper must
    surface the failure via ``replay_errors`` so an agent (or
    operator) sees that recovery is incomplete.
    """
    monkeypatch.setenv("BICAMERAL_DATA_PATH", str(tmp_path))
    monkeypatch.setenv("SURREAL_URL", "memory://")
    # Deliberately do NOT create tmp_path/.bicameral/events/ — that's
    # the failure mode under test.

    from cli.reset_cli import run_noninteractive_reset

    rc = run_noninteractive_reset(wipe_mode="ledger", replay_from_events=True)
    out = capsys.readouterr().out
    assert rc == 0, out  # wipe still succeeds; only replay fails
    payload = json.loads(out)
    assert payload["wiped"] is True
    assert payload["events_replayed"] == 0
    assert payload["replay_errors"], (
        "missing events dir must surface in replay_errors — silent "
        "zero-replay is the bug we're guarding against"
    )
    assert any("events dir not found" in err for err in payload["replay_errors"]), (
        f"replay_errors did not name the missing-events-dir failure: {payload['replay_errors']!r}"
    )


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
