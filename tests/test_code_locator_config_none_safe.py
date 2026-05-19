"""Phase 2B (#368): code_locator/config.py is None-safe for direct construction.

`CodeLocatorConfig.sqlite_db` default is now `None`. `resolve_paths()`
substitutes the locator-resolved path when called with `sqlite_db is
None`, so direct-construction callers that bypass `load_config()` still
get a usable path. Env-var override and pre-set string values are
unaffected.
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
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


def test_load_config_substitutes_locator_path_when_unset(git_repo: Path, monkeypatch) -> None:
    """`load_config()` end-to-end: env unset → sqlite_db resolves to the
    locator's project-scoped code-graph.db path.
    """
    import ledger_locator
    from code_locator.config import load_config

    monkeypatch.chdir(git_repo)
    expected = str(ledger_locator.resolve_code_graph_path(repo_path=git_repo))

    config = load_config()

    assert config.sqlite_db == expected


def test_resolve_paths_handles_none_sqlite_db(git_repo: Path, monkeypatch) -> None:
    """Direct construction of CodeLocatorConfig(sqlite_db=None) + a
    `resolve_paths()` call still yields a non-None usable path. Guards
    against future regressions where a caller bypasses load_config().
    """
    from code_locator.config import CodeLocatorConfig

    monkeypatch.chdir(git_repo)
    config = CodeLocatorConfig(sqlite_db=None).resolve_paths()

    assert config.sqlite_db is not None
    assert isinstance(config.sqlite_db, str)
    assert config.sqlite_db.endswith("code-graph.db")


def test_env_var_still_wins_over_locator(git_repo: Path, monkeypatch, tmp_path: Path) -> None:
    """Setting CODE_LOCATOR_SQLITE_DB still overrides the locator. The
    env-var contract pre-dates the locator and must keep working.
    """
    from code_locator.config import load_config

    explicit = str(tmp_path / "explicit.db")
    monkeypatch.setenv("CODE_LOCATOR_SQLITE_DB", explicit)
    monkeypatch.chdir(git_repo)

    config = load_config()

    assert config.sqlite_db == explicit


def test_resolve_paths_outside_git_repo_raises(tmp_path: Path, monkeypatch) -> None:
    """Outside a git repo with no `CODE_LOCATOR_SQLITE_DB` override, the
    locator raises `ProjectIdResolutionError`; `resolve_paths` propagates
    it. Per decision:c2eqcwimhe4lpaexrddw, behavior is undefined in
    unsupported environments — naming the problem (not-a-git-repo) is
    strictly better than silently writing to a hardcoded parallel path
    that drifts from the locator's canonical layout (and isn't Windows-
    friendly to begin with). Tests that need a custom path set
    `CODE_LOCATOR_SQLITE_DB`; production callers set `SURREAL_URL`.
    """
    import ledger_locator
    from code_locator.config import CodeLocatorConfig

    monkeypatch.chdir(tmp_path)

    with pytest.raises(ledger_locator.ProjectIdResolutionError) as exc:
        CodeLocatorConfig(sqlite_db=None).resolve_paths()

    # The error message must name the actual problem (git-only assumption),
    # not bury it in a stack trace.
    assert "bicameral currently supports git only" in str(exc.value)


def test_resolve_paths_outside_git_repo_works_with_env_override(
    tmp_path: Path, monkeypatch
) -> None:
    """The escape hatch from the previous test: set CODE_LOCATOR_SQLITE_DB
    via env (which load_config picks up) and `resolve_paths` happily uses
    the explicit string without ever consulting the locator.
    """
    from code_locator.config import load_config

    explicit = str(tmp_path / "explicit.db")
    monkeypatch.setenv("CODE_LOCATOR_SQLITE_DB", explicit)
    monkeypatch.chdir(tmp_path)  # not a git repo

    config = load_config()  # must NOT raise

    assert config.sqlite_db == explicit
