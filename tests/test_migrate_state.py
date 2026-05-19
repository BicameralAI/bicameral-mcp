"""Tests for `bicameral-mcp migrate-state` (#368 Phase 3).

Moves project-scoped state from `<repo>/.bicameral/` into the
locator-resolved project dir at `~/.bicameral/projects/<id>/`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cli import migrate_state  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _seed_full_legacy_layout(repo: Path) -> dict[str, bytes]:
    """Populate every source path with deterministic random-ish bytes.
    Returns a dict of relpath → contents for downstream byte-for-byte
    assertions."""
    payloads: dict[str, bytes] = {}
    (repo / ".bicameral").mkdir(exist_ok=True)
    (repo / ".bicameral" / "local").mkdir(exist_ok=True)
    (repo / ".bicameral" / "pending-transcripts").mkdir(exist_ok=True)
    (repo / ".bicameral" / "processed-transcripts").mkdir(exist_ok=True)

    files = {
        ".bicameral/ledger.db": b"ledger-bytes-" + os.urandom(64),
        ".bicameral/local/code-graph.db": b"cg-bytes-" + os.urandom(64),
        ".bicameral/local/code-graph.db-shm": b"shm-" + os.urandom(32),
        ".bicameral/local/code-graph.db-wal": b"wal-" + os.urandom(32),
        ".bicameral/local/bm25_index.pkl": b"bm25-" + os.urandom(64),
        ".bicameral/local/watermark": b'{"peer@example.com": 17}',
        ".bicameral/pending-transcripts/sess1.jsonl": b'{"sid":"s1"}\n',
        ".bicameral/pending-transcripts/sess2.jsonl": b'{"sid":"s2"}\n',
        ".bicameral/processed-transcripts/sess0.jsonl": b'{"sid":"s0"}\n',
    }
    for rel, body in files.items():
        (repo / rel).write_bytes(body)
        payloads[rel] = body
    return payloads


def _project_dir(repo: Path) -> Path:
    import ledger_locator

    return ledger_locator.project_dir_for(repo)


def test_moves_ledger_and_code_graph_in_one_pass(git_repo: Path) -> None:
    seeded = _seed_full_legacy_layout(git_repo)
    pdir = _project_dir(git_repo)

    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0

    expected_pairs = {
        ".bicameral/ledger.db": "ledger.db",
        ".bicameral/local/code-graph.db": "code-graph.db",
        ".bicameral/local/code-graph.db-shm": "code-graph.db-shm",
        ".bicameral/local/code-graph.db-wal": "code-graph.db-wal",
        ".bicameral/local/bm25_index.pkl": "bm25_index.pkl",
        ".bicameral/local/watermark": "watermark",
        ".bicameral/pending-transcripts/sess1.jsonl": "pending-transcripts/sess1.jsonl",
        ".bicameral/pending-transcripts/sess2.jsonl": "pending-transcripts/sess2.jsonl",
        ".bicameral/processed-transcripts/sess0.jsonl": "processed-transcripts/sess0.jsonl",
    }
    for src_rel, dest_rel in expected_pairs.items():
        assert not (git_repo / src_rel).exists(), f"source survived: {src_rel}"
        dest = pdir / dest_rel
        assert dest.exists(), f"dest missing: {dest_rel}"
        assert dest.read_bytes() == seeded[src_rel]

    # Empty source dirs should be cleaned up.
    assert not (git_repo / ".bicameral" / "local").exists()
    assert not (git_repo / ".bicameral" / "pending-transcripts").exists()
    assert not (git_repo / ".bicameral" / "processed-transcripts").exists()


def test_idempotent_second_run_is_noop(git_repo: Path, capsys) -> None:
    _seed_full_legacy_layout(git_repo)
    pdir = _project_dir(git_repo)
    migrate_state.main(["--repo", str(git_repo), "--auto"])
    capsys.readouterr()  # discard first-run output
    mtimes_before = {p: p.stat().st_mtime for p in pdir.rglob("*") if p.is_file()}
    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing to migrate." in out
    mtimes_after = {p: p.stat().st_mtime for p in pdir.rglob("*") if p.is_file()}
    assert mtimes_after == mtimes_before, "second run touched destination files"


def test_collision_archives_destination(git_repo: Path, tmp_path: Path) -> None:
    _seed_full_legacy_layout(git_repo)
    pdir = _project_dir(git_repo)
    pdir.mkdir(parents=True, exist_ok=True)
    # Pre-populate the destination with DIFFERENT content so the migrator
    # has to archive the existing file before moving the source in.
    existing = b"i-am-an-older-ledger"
    (pdir / "ledger.db").write_bytes(existing)

    archive_dir = tmp_path / "archive"
    rc = migrate_state.main(
        [
            "--repo",
            str(git_repo),
            "--auto",
            "--archive-dir",
            str(archive_dir),
        ]
    )
    assert rc == 0
    archives = list(archive_dir.glob("ledger.db.*.bak"))
    assert len(archives) == 1
    assert archives[0].read_bytes() == existing
    # Source no longer exists, dest now holds the source's bytes.
    assert not (git_repo / ".bicameral" / "ledger.db").exists()
    assert (pdir / "ledger.db").read_bytes() != existing


def test_dry_run_writes_nothing(git_repo: Path, capsys) -> None:
    seeded = _seed_full_legacy_layout(git_repo)
    pdir = _project_dir(git_repo)
    # Make sure the destination dir doesn't exist before the dry-run so
    # we can assert it stays absent.
    if pdir.exists():
        import shutil

        shutil.rmtree(pdir, ignore_errors=True)
    rc = migrate_state.main(["--repo", str(git_repo), "--dry-run", "--auto"])
    assert rc == 0
    # Sources unchanged
    for rel, body in seeded.items():
        assert (git_repo / rel).read_bytes() == body
    # No destinations created
    assert not pdir.exists() or not any(pdir.iterdir())
    # Output enumerates planned moves
    out = capsys.readouterr().out
    assert "[dry-run]" in out


def test_missing_source_skips_silently(git_repo: Path, capsys) -> None:
    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Nothing to migrate." in out


def test_partial_state_migrates_what_exists(git_repo: Path) -> None:
    (git_repo / ".bicameral").mkdir()
    (git_repo / ".bicameral" / "ledger.db").write_bytes(b"only-ledger-here")
    pdir = _project_dir(git_repo)

    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0
    assert (pdir / "ledger.db").read_bytes() == b"only-ledger-here"
    assert not (pdir / "code-graph.db").exists()


def test_auto_flag_skips_prompts(git_repo: Path, monkeypatch) -> None:
    _seed_full_legacy_layout(git_repo)

    def _explode(*a, **kw):
        raise AssertionError("interactive prompt should be skipped under --auto")

    monkeypatch.setattr("builtins.input", _explode)
    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0


def test_archive_dir_defaults_under_home(git_repo: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-import ledger_locator under the new HOME so STATE_ROOT picks up
    # the redirected path.
    import importlib

    import ledger_locator

    importlib.reload(ledger_locator)

    _seed_full_legacy_layout(git_repo)
    pdir = ledger_locator.project_dir_for(git_repo)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "ledger.db").write_bytes(b"older-content")

    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0

    expected_archive_root = tmp_path / ".bicameral" / "archive"
    archives = list(expected_archive_root.rglob("ledger.db.*.bak"))
    assert len(archives) == 1


def test_moves_bm25_watermark_and_transcript_queues(git_repo: Path) -> None:
    """Explicit coverage of the four R3-added sources (the locator added
    these resolvers but pre-#368 builds wrote them under `<repo>/.bicameral/`
    — the migration must move them with byte-for-byte fidelity)."""
    seeded = _seed_full_legacy_layout(git_repo)
    pdir = _project_dir(git_repo)

    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0
    assert (pdir / "bm25_index.pkl").read_bytes() == seeded[".bicameral/local/bm25_index.pkl"]
    assert (pdir / "watermark").read_bytes() == seeded[".bicameral/local/watermark"]
    assert (pdir / "pending-transcripts" / "sess1.jsonl").exists()
    assert (pdir / "pending-transcripts" / "sess2.jsonl").exists()
    assert (pdir / "processed-transcripts" / "sess0.jsonl").exists()


def test_legacy_user_global_ledger_moves_on_first_project(
    git_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """The v0.15.x layout put `~/.bicameral/ledger.db` outside any project
    namespace. The first project's migrate-state claims it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib

    import cli.migrate_state as ms
    import ledger_locator

    importlib.reload(ledger_locator)
    importlib.reload(ms)

    legacy = tmp_path / ".bicameral" / "ledger.db"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"user-global-ledger")

    rc = ms.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0
    pdir = ledger_locator.project_dir_for(git_repo)
    assert (pdir / "ledger.db").read_bytes() == b"user-global-ledger"
    assert not legacy.exists()


def test_legacy_user_global_ledger_already_claimed_is_noop(
    git_repo: Path, tmp_path: Path, monkeypatch
) -> None:
    """If the user-global ledger no longer exists (already moved on a
    prior project) and the current project doesn't have its own legacy
    state, migrate runs as a no-op."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib

    import cli.migrate_state as ms
    import ledger_locator

    importlib.reload(ledger_locator)
    importlib.reload(ms)

    # ~/.bicameral/ledger.db does NOT exist; just make sure the rest of
    # the layout is empty too.
    rc = ms.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0


def test_partitions_config_yaml_keys(git_repo: Path) -> None:
    """R4 addition: when `<repo>/.bicameral/config.yaml` predates the split,
    migrate-state partitions the per-operator keys into operator.yaml and
    keeps the team-identity keys in config.yaml."""
    (git_repo / ".bicameral").mkdir(exist_ok=True)
    (git_repo / ".bicameral" / "config.yaml").write_text(
        "mode: team\n"
        "guided: true\n"
        "telemetry: false\n"
        "channel: stable\n"
        "team:\n"
        "  backend: google_drive\n"
        "  folder_id: x\n"
        "  role: member\n",
        encoding="utf-8",
    )
    pdir = _project_dir(git_repo)

    rc = migrate_state.main(["--repo", str(git_repo), "--auto"])
    assert rc == 0

    import yaml

    team = yaml.safe_load((git_repo / ".bicameral" / "config.yaml").read_text())
    op = yaml.safe_load((pdir / "operator.yaml").read_text())

    assert team["mode"] == "team"
    assert team["team"] == {"backend": "google_drive", "folder_id": "x"}
    # Per-operator keys must leave the committed file.
    for k in ("guided", "telemetry", "channel"):
        assert k not in team

    assert op["guided"] is True
    assert op["telemetry"] is False
    assert op["channel"] == "stable"
    assert op["team"] == {"role": "member"}
