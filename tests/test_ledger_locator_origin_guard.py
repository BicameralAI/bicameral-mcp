"""Unit tests for `ledger_locator` origin-guard — #368 Phase 1.

The origin guard writes `<project_dir>/origin.txt` on first resolve and refuses
subsequent resolves when the recorded origin disagrees with the current one
(modulo a documented env-var escape hatch).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_locator_env(monkeypatch):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


def test_first_resolve_writes_origin_txt(git_repo: Path) -> None:
    import ledger_locator

    ledger_locator.resolve_ledger_url(repo_path=git_repo)

    project_dir = ledger_locator.project_dir_for(repo_path=git_repo)
    origin_file = project_dir / "origin.txt"

    assert origin_file.exists()
    content = origin_file.read_text().strip()

    # The content is the absolute common-dir path for the repo.
    common_dir = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    expected = str(Path(common_dir if Path(common_dir).is_absolute() else git_repo / common_dir).resolve())
    assert content == expected


def test_collision_with_different_origin_raises(git_repo: Path) -> None:
    import ledger_locator

    project_dir = ledger_locator.project_dir_for(repo_path=git_repo)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "origin.txt").write_text("/somewhere/else/.git\n")

    with pytest.raises(ledger_locator.ProjectIdCollisionError) as exc:
        ledger_locator.resolve_ledger_url(repo_path=git_repo)

    msg = str(exc.value)
    # Surfaces BOTH paths so the operator can triage.
    assert "/somewhere/else/.git" in msg
    assert str(git_repo) in msg or ".git" in msg


def test_collision_override_logs_and_proceeds(
    git_repo: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    import ledger_locator

    project_dir = ledger_locator.project_dir_for(repo_path=git_repo)
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "origin.txt").write_text("/somewhere/else/.git\n")

    monkeypatch.setenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", "1")

    with caplog.at_level(logging.WARNING, logger="ledger_locator"):
        url = ledger_locator.resolve_ledger_url(repo_path=git_repo)

    assert url.startswith("surrealkv://")
    warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("/somewhere/else/.git" in r.getMessage() for r in warns), (
        f"expected a WARN naming the foreign origin; got: {[r.getMessage() for r in warns]}"
    )
