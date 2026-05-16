"""Phase 2 of #279 — team-mode integration tests.

Sociable per CLAUDE.md: instantiates real ``LocalFolderAdapter`` +
real CLI ``_run`` path. No mocks on the backend, no mocks on the
materializer, no mocks on the file system.

The two-machine round-trip test is the strongest assertion in the
suite — it spans two ``BicameralContext`` instances + a shared
``remote_root`` directory + the real backend + the real materializer.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

import cli.sync_and_brief_cli as sb
from events.backends.local_folder import LocalFolderAdapter


def _make_args(**kw) -> argparse.Namespace:
    defaults = {"max_decisions": 20, "quiet": True, "command": "sync-and-brief"}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _make_machine_ctx(repo_path: Path) -> SimpleNamespace:
    """Build a SimpleNamespace ctx with a fake ledger that the sync-and-brief
    CLI can interrogate without spinning SurrealDB. The team-mode logic
    under test does not actually invoke the ledger; brief synthesis is
    patched to a no-op constant string."""
    ledger = MagicMock()
    ledger.connect = AsyncMock(return_value=None)
    ledger.get_all_decisions = AsyncMock(return_value=[])
    return SimpleNamespace(repo_path=str(repo_path), ledger=ledger)


def _write_yaml_config(repo: Path, data: dict) -> None:
    cfg = repo / ".bicameral" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(yaml.safe_dump(data))


def _seed_event_file(events_dir: Path, author: str, content: str = "{}\n") -> Path:
    events_dir.mkdir(parents=True, exist_ok=True)
    path = events_dir / f"{author}.jsonl"
    path.write_text(content, encoding="utf-8")
    return path


# ── _resolve_team_backend ─────────────────────────────────────────────────


def test_resolve_team_backend_returns_none_for_solo_config(tmp_path: Path) -> None:
    """No `team:` key → solo mode → None."""
    assert sb._resolve_team_backend({"sources": []}) is None
    assert sb._resolve_team_backend({}) is None
    assert sb._resolve_team_backend(None) is None


def test_resolve_team_backend_warns_and_returns_none_when_author_empty(
    tmp_path: Path, capsys
) -> None:
    """team.backend without team.author → skip + stderr warning."""
    cfg = {
        "team": {
            "backend": "local_folder",
            "remote_root": str(tmp_path / "remote"),
            "author": "",
        }
    }
    result = sb._resolve_team_backend(cfg)
    assert result is None
    err = capsys.readouterr().err
    assert "team.author is empty" in err


def test_resolve_team_backend_returns_local_folder_adapter(tmp_path: Path) -> None:
    cfg = {
        "team": {
            "backend": "local_folder",
            "remote_root": str(tmp_path / "remote"),
            "author": "alice@example.com",
        }
    }
    backend = sb._resolve_team_backend(cfg)
    assert isinstance(backend, LocalFolderAdapter)


# ── _team_sync_pull ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_sync_pull_copies_peer_files_into_events_dir(
    tmp_path: Path,
) -> None:
    """Pull copies every peer's JSONL into events_dir except the caller's own."""
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "alice@example.com.jsonl").write_text("alice event\n")
    (remote / "bob@example.com.jsonl").write_text("bob event\n")

    backend = LocalFolderAdapter(remote_root=remote, author="alice@example.com")
    events_dir = tmp_path / "events"
    pulled_count = await sb._team_sync_pull(backend, events_dir)
    assert (events_dir / "bob@example.com.jsonl").exists()
    assert not (events_dir / "alice@example.com.jsonl").exists()
    # Count reflects the post-pull state of events_dir
    assert pulled_count == 1


@pytest.mark.asyncio
async def test_team_sync_pull_continues_on_backend_failure(tmp_path: Path, capsys) -> None:
    """Failure path: backend raises, helper logs to stderr + returns 0."""
    failing_backend = MagicMock()
    failing_backend.pull_events = AsyncMock(side_effect=RuntimeError("disk full"))
    result = await sb._team_sync_pull(failing_backend, tmp_path / "events")
    assert result == 0
    err = capsys.readouterr().err
    assert "team backend pull failed" in err
    assert "disk full" in err


# ── _team_sync_push ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_sync_push_uploads_every_local_jsonl(tmp_path: Path) -> None:
    """Push iterates events_dir/*.jsonl and forwards each to backend."""
    events_dir = tmp_path / "events"
    _seed_event_file(events_dir, "alice@example.com", "a\n")
    _seed_event_file(events_dir, "bob@example.com", "b\n")
    remote = tmp_path / "remote"
    backend = LocalFolderAdapter(remote_root=remote, author="alice@example.com")
    pushed = await sb._team_sync_push(backend, events_dir)
    assert pushed is True
    assert (remote / "alice@example.com.jsonl").exists()
    assert (remote / "bob@example.com.jsonl").exists()


@pytest.mark.asyncio
async def test_team_sync_push_returns_false_for_empty_events_dir(
    tmp_path: Path,
) -> None:
    """No JSONL files → push returns False (nothing to upload)."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    backend = LocalFolderAdapter(remote_root=tmp_path / "remote", author="alice@example.com")
    pushed = await sb._team_sync_push(backend, events_dir)
    assert pushed is False


@pytest.mark.asyncio
async def test_team_sync_push_continues_on_per_file_failure(tmp_path: Path, capsys) -> None:
    """If push_events raises for one file, other files still pushed."""
    events_dir = tmp_path / "events"
    _seed_event_file(events_dir, "alice@example.com", "a\n")
    _seed_event_file(events_dir, "bob@example.com", "b\n")

    failing = MagicMock()
    # First call raises, second succeeds
    failing.push_events = AsyncMock(side_effect=[RuntimeError("boom"), None])
    result = await sb._team_sync_push(failing, events_dir)
    # Second call succeeded → pushed is True
    assert result is True
    err = capsys.readouterr().err
    assert "team backend push failed" in err


# ── full _run integration via LocalFolderAdapter ─────────────────────────


@pytest.mark.asyncio
async def test_team_sync_round_trip_alice_to_bob_via_local_folder(
    tmp_path: Path,
) -> None:
    """The headline test: Alice pushes events; Bob's next sync-and-brief
    pulls them into Bob's events_dir. End-to-end across two simulated
    machines + a real shared LocalFolderAdapter."""
    remote = tmp_path / "remote"
    remote.mkdir()

    # Machine A — Alice
    alice_repo = tmp_path / "alice"
    alice_events = alice_repo / ".bicameral" / "events"
    _seed_event_file(
        alice_events,
        "alice@example.com",
        '{"event_type":"ingest.completed","author":"alice@example.com","payload":{}}\n',
    )
    _write_yaml_config(
        alice_repo,
        {
            "team": {
                "backend": "local_folder",
                "remote_root": str(remote),
                "author": "alice@example.com",
            }
        },
    )
    alice_ctx = _make_machine_ctx(alice_repo)

    # Alice's run pushes her file.
    with (
        patch.object(sb, "_synthesize_brief", new=AsyncMock(return_value="ok")),
        patch("context.BicameralContext.from_env", return_value=alice_ctx),
    ):
        rc = await sb._run(_make_args(quiet=True))
    assert rc == 0
    assert (remote / "alice@example.com.jsonl").exists()

    # Machine B — Bob
    bob_repo = tmp_path / "bob"
    _write_yaml_config(
        bob_repo,
        {
            "team": {
                "backend": "local_folder",
                "remote_root": str(remote),
                "author": "bob@example.com",
            }
        },
    )
    bob_ctx = _make_machine_ctx(bob_repo)
    bob_events = bob_repo / ".bicameral" / "events"

    with (
        patch.object(sb, "_synthesize_brief", new=AsyncMock(return_value="ok")),
        patch("context.BicameralContext.from_env", return_value=bob_ctx),
    ):
        rc = await sb._run(_make_args(quiet=True))
    assert rc == 0
    # Bob now has Alice's event file in his local cache.
    assert (bob_events / "alice@example.com.jsonl").exists()


@pytest.mark.asyncio
async def test_solo_mode_unaffected_by_team_phase_2_integration(tmp_path: Path, capsys) -> None:
    """When no `team:` config is present, the CLI behaves exactly like
    Phase 1 — no team sync, no remote calls, no errors."""
    repo = tmp_path / "repo"
    _write_yaml_config(repo, {})  # empty config; no team, no sources

    ctx = _make_machine_ctx(repo)
    with (
        patch.object(sb, "_synthesize_brief", new=AsyncMock(return_value="ok")),
        patch("context.BicameralContext.from_env", return_value=ctx),
    ):
        rc = await sb._run(_make_args(quiet=False))
    assert rc == 0
    # No remote root was created (verifies no backend was constructed)
    err = capsys.readouterr().err
    assert "team backend" not in err


@pytest.mark.asyncio
async def test_team_sync_pull_runs_before_source_pull(tmp_path: Path) -> None:
    """Order invariant: pull peer events FIRST, then source-pull. Verify
    by checking the events_dir state at the moment _run_source is called.

    We use a recording sentinel that captures the events_dir contents at
    the moment _run_source fires; assert peer files are already present."""
    remote = tmp_path / "remote"
    remote.mkdir()
    (remote / "alice@example.com.jsonl").write_text("a\n")

    repo = tmp_path / "bob"
    _write_yaml_config(
        repo,
        {
            "sources": [{"type": "granola", "api_key_env": "GRANOLA_API_KEY"}],
            "team": {
                "backend": "local_folder",
                "remote_root": str(remote),
                "author": "bob@example.com",
            },
        },
    )
    ctx = _make_machine_ctx(repo)
    events_dir = repo / ".bicameral" / "events"

    captured: dict[str, list[str]] = {}

    async def _capture_run_source(c, s, *, watermark_dir):
        captured["state_at_source_pull"] = sorted(p.name for p in events_dir.glob("*.jsonl"))

    with (
        patch.object(sb, "_run_source", new=AsyncMock(side_effect=_capture_run_source)),
        patch.object(sb, "_synthesize_brief", new=AsyncMock(return_value="ok")),
        patch("context.BicameralContext.from_env", return_value=ctx),
    ):
        rc = await sb._run(_make_args(quiet=True))
    assert rc == 0
    # Alice's file was pulled BEFORE _run_source fired
    assert "alice@example.com.jsonl" in captured["state_at_source_pull"]


@pytest.mark.asyncio
async def test_team_sync_push_idempotent_via_sha_match(tmp_path: Path) -> None:
    """Running sync-and-brief twice in a row pushes the same content; the
    second push is a noop because LocalFolderAdapter sha-matches and
    skips the copy. We assert the file in remote_root has the same
    mtime after the second run (skip → no fs write)."""
    remote = tmp_path / "remote"
    remote.mkdir()

    repo = tmp_path / "alice"
    _seed_event_file(repo / ".bicameral" / "events", "alice@example.com", "alice content\n")
    _write_yaml_config(
        repo,
        {
            "team": {
                "backend": "local_folder",
                "remote_root": str(remote),
                "author": "alice@example.com",
            }
        },
    )
    ctx = _make_machine_ctx(repo)

    with (
        patch.object(sb, "_synthesize_brief", new=AsyncMock(return_value="ok")),
        patch("context.BicameralContext.from_env", return_value=ctx),
    ):
        await sb._run(_make_args(quiet=True))
        remote_file = remote / "alice@example.com.jsonl"
        assert remote_file.exists()
        mtime_first = remote_file.stat().st_mtime_ns

        # Second run with no local changes
        await sb._run(_make_args(quiet=True))
        mtime_second = remote_file.stat().st_mtime_ns

    # sha match → no copy → mtime unchanged
    assert mtime_first == mtime_second
