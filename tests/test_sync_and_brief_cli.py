"""Tests for the bicameral-mcp sync-and-brief CLI (#279 Phase 1 Phase C).

Mocks adapter pull, handle_ingest, handle_preflight, and ledger.get_all_decisions
at narrow seams so the orchestration logic can be exercised without spinning
up a real SurrealDB or hitting any external API.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import cli.sync_and_brief_cli as sb


def _make_args(*, max_decisions: int = 20, quiet: bool = False) -> argparse.Namespace:
    return argparse.Namespace(command="sync-and-brief", max_decisions=max_decisions, quiet=quiet)


def _make_ctx(repo_path: Path) -> SimpleNamespace:
    """Sociable ctx — SimpleNamespace per CLAUDE.md guidance."""
    ledger = MagicMock()
    ledger.connect = AsyncMock(return_value=None)
    ledger.get_all_decisions = AsyncMock(return_value=[])
    return SimpleNamespace(repo_path=str(repo_path), ledger=ledger)


# ── argparse wiring ──────────────────────────────────────────────────────


def test_sync_and_brief_subcommand_registered(tmp_path: Path, monkeypatch) -> None:
    """Smoke: invoking `sync-and-brief --help` from the top-level argparse
    succeeds and the help text contains the subcommand description.

    Skipped if the ``mcp`` package isn't installed in the test venv (it's
    a runtime dep, not a test-collection dep — the rest of this module
    exercises the subcommand without importing server.py)."""
    pytest.importorskip("mcp.server.stdio")
    monkeypatch.chdir(tmp_path)
    import server

    with pytest.raises(SystemExit) as exc_info:
        server.cli_main(["sync-and-brief", "--help"])
    assert exc_info.value.code == 0


def test_argparser_accepts_max_decisions_and_quiet_flags() -> None:
    """The subcommand's argparse accepts --max-decisions and --quiet."""
    parser = argparse.ArgumentParser()
    sb._build_argparser(parser)
    args = parser.parse_args(["--max-decisions", "5", "--quiet"])
    assert args.max_decisions == 5
    assert args.quiet is True


# ── happy path: no sources configured ─────────────────────────────────────


def test_sync_and_brief_exits_zero_when_no_sources_configured(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """A repo without .bicameral/config.yaml (or with config lacking
    `sources:`) prints the hint and exits 0."""
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    with patch.object(sb, "_synthesize_brief", new=AsyncMock(return_value="")):
        rc = sb.main(_make_args())
    assert rc == 0
    out = capsys.readouterr().out
    assert "No sources configured" in out


# ── per-source orchestration ──────────────────────────────────────────────


def test_run_source_calls_adapter_pull_and_handle_ingest_then_confirm(
    tmp_path: Path,
) -> None:
    """Happy path: adapter pulls 2 payloads → handle_ingest awaited twice
    → confirm_watermark called once."""
    fake_adapter = MagicMock()
    fake_adapter.name = "granola"
    fake_adapter.pull.return_value = [{"payload": 1}, {"payload": 2}]
    fake_adapter.confirm_watermark = MagicMock()

    ctx = _make_ctx(tmp_path)
    fake_handle_ingest = AsyncMock(return_value=None)

    with (
        patch.dict(sb.__dict__),  # don't pollute module state across tests
        patch("events.sources.ADAPTERS", {"granola": lambda: fake_adapter}),
        patch("handlers.ingest.handle_ingest", new=fake_handle_ingest),
    ):
        import asyncio

        asyncio.run(
            sb._run_source(
                ctx,
                source={"type": "granola", "api_key_env": "X"},
                watermark_dir=tmp_path / "watermarks",
            )
        )

    fake_adapter.pull.assert_called_once()
    assert fake_handle_ingest.await_count == 2
    fake_adapter.confirm_watermark.assert_called_once()


def test_run_source_skips_watermark_advance_on_ingest_failure(
    tmp_path: Path,
) -> None:
    """If handle_ingest raises, confirm_watermark is NOT called."""
    fake_adapter = MagicMock()
    fake_adapter.name = "granola"
    fake_adapter.pull.return_value = [{"payload": 1}]
    fake_adapter.confirm_watermark = MagicMock()

    ctx = _make_ctx(tmp_path)
    fake_handle_ingest = AsyncMock(side_effect=RuntimeError("ingest blew up"))

    with (
        patch("events.sources.ADAPTERS", {"granola": lambda: fake_adapter}),
        patch("handlers.ingest.handle_ingest", new=fake_handle_ingest),
    ):
        import asyncio

        asyncio.run(
            sb._run_source(
                ctx,
                source={"type": "granola"},
                watermark_dir=tmp_path / "watermarks",
            )
        )

    fake_adapter.pull.assert_called_once()
    fake_adapter.confirm_watermark.assert_not_called()


def test_run_source_exits_gracefully_on_missing_api_key(tmp_path: Path, capsys) -> None:
    """MissingApiKeyError from pull is caught; other sources should still run."""
    from events.sources import MissingApiKeyError

    fake_adapter = MagicMock()
    fake_adapter.pull.side_effect = MissingApiKeyError(
        "Granola adapter: env var 'GRANOLA_API_KEY' is unset"
    )

    ctx = _make_ctx(tmp_path)
    with patch("events.sources.ADAPTERS", {"granola": lambda: fake_adapter}):
        import asyncio

        # Must not raise
        asyncio.run(
            sb._run_source(
                ctx,
                source={"type": "granola"},
                watermark_dir=tmp_path / "watermarks",
            )
        )

    err = capsys.readouterr().err
    assert "GRANOLA_API_KEY" in err


def test_run_source_warns_on_unknown_source_type(tmp_path: Path, capsys) -> None:
    ctx = _make_ctx(tmp_path)
    import asyncio

    with patch("events.sources.ADAPTERS", {}):
        asyncio.run(
            sb._run_source(
                ctx,
                source={"type": "unknown-source"},
                watermark_dir=tmp_path / "watermarks",
            )
        )
    err = capsys.readouterr().err
    assert "unknown source type" in err
    assert "unknown-source" in err


# ── synthesize_brief integration ──────────────────────────────────────────


def test_synthesize_brief_calls_preflight_and_renders_brief(
    tmp_path: Path,
) -> None:
    """_synthesize_brief calls handle_preflight + build_project_pulse and
    returns the rendered Project Pulse brief (#437 Phase 2 rewire)."""
    ctx = _make_ctx(tmp_path)
    fake_preflight = AsyncMock(return_value=SimpleNamespace(findings=[]))

    import asyncio

    with patch("handlers.preflight.handle_preflight", new=fake_preflight):
        result = asyncio.run(sb._synthesize_brief(ctx, max_decisions=20))

    # #437 Phase 2: the brief is now the shared Project Pulse render.
    assert "Bicameral Brief" in result
    assert "read-only data" in result  # prompt-injection data-framing line
    fake_preflight.assert_awaited_once()
    # build_project_pulse reads decisions from the ledger.
    ctx.ledger.get_all_decisions.assert_awaited()


def test_synthesize_brief_continues_when_preflight_fails(
    tmp_path: Path,
) -> None:
    """Drift is best-effort; preflight failure does NOT crash the brief."""
    ctx = _make_ctx(tmp_path)
    fake_preflight = AsyncMock(side_effect=RuntimeError("preflight wedged"))

    import asyncio

    with patch("handlers.preflight.handle_preflight", new=fake_preflight):
        result = asyncio.run(sb._synthesize_brief(ctx, max_decisions=20))

    # The brief still renders — drift simply contributes zero findings.
    assert "Bicameral Brief" in result


# ── quiet flag ───────────────────────────────────────────────────────────


def test_quiet_flag_suppresses_stdout(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    # Write a config with no sources so main() exits the "no sources" path
    # and we can specifically test that --quiet suppresses that hint too.
    (tmp_path / ".bicameral").mkdir()
    (tmp_path / ".bicameral" / "config.yaml").write_text("# empty\n")
    rc = sb.main(_make_args(quiet=True))
    assert rc == 0
    captured = capsys.readouterr()
    assert "No sources configured" not in captured.out


# ── error handling ───────────────────────────────────────────────────────


def test_main_returns_1_and_logs_on_unexpected_exception(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    """An unexpected exception in _run is caught, logged, exit 1."""
    monkeypatch.setenv("REPO_PATH", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with patch.object(sb, "_run", new=AsyncMock(side_effect=RuntimeError("boom"))):
        rc = sb.main(_make_args())
    assert rc == 1
    err = capsys.readouterr().err
    assert "unexpected error" in err
    assert "boom" in err
    # Error log file written
    log_path = tmp_path / ".bicameral" / "cli-errors.log"
    assert log_path.exists()
    assert "boom" in log_path.read_text()
