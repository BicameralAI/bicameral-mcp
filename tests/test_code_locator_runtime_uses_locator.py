"""Phase 2B (#368): code_locator_runtime.ensure_runtime_env delegates to the Locator.

Replaces the prior repo-local fallback (`<repo>/.bicameral/code-graph.db`)
with a locator-resolved per-project path. Pre-existing
`CODE_LOCATOR_SQLITE_DB` env values are preserved; non-git invocations
leave the env unset (the None-safe config-load handles the fallback).
"""

from __future__ import annotations

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
    monkeypatch.delenv("REPO_PATH", raising=False)
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


def test_runtime_sets_env_from_locator(git_repo: Path, monkeypatch) -> None:
    """In a git repo with CODE_LOCATOR_SQLITE_DB unset, `ensure_runtime_env`
    pre-populates the env from the locator's project-scoped default.
    """
    import ledger_locator
    from code_locator_runtime import ensure_runtime_env

    monkeypatch.chdir(git_repo)
    expected = str(ledger_locator.resolve_code_graph_path(repo_path=git_repo))

    ensure_runtime_env()

    assert os.environ.get("CODE_LOCATOR_SQLITE_DB") == expected


def test_runtime_respects_existing_env(git_repo: Path, monkeypatch, tmp_path: Path) -> None:
    """If CODE_LOCATOR_SQLITE_DB is already set, ensure_runtime_env does
    not overwrite it. setdefault semantics.
    """
    from code_locator_runtime import ensure_runtime_env

    monkeypatch.chdir(git_repo)
    explicit = str(tmp_path / "explicit.db")
    monkeypatch.setenv("CODE_LOCATOR_SQLITE_DB", explicit)

    ensure_runtime_env()

    assert os.environ.get("CODE_LOCATOR_SQLITE_DB") == explicit


def test_runtime_silent_when_not_in_git(tmp_path: Path, monkeypatch) -> None:
    """Outside a git repo, the locator raises ProjectIdResolutionError;
    ensure_runtime_env catches and leaves the env unset rather than
    crashing the MCP server boot. The None-safe config-load handles
    the fallback at the next layer.
    """
    from code_locator_runtime import ensure_runtime_env

    monkeypatch.chdir(tmp_path)

    # Must not raise even though tmp_path is not a git repo.
    ensure_runtime_env()

    # Env stays unset for None-safe fallback downstream.
    assert "CODE_LOCATOR_SQLITE_DB" not in os.environ
