"""Sociable test for #334 option C lite — validate_symbols returns
the git SHA the symbol index was built against, so callers can detect
snapshot drift vs authoritative_sha before bind.

Real SymbolDB, real build_index, real git subprocess, real
record_index_state — no MagicMock. Mirrors the
``codegenome_continuity_service._fresh_adapter`` pattern from CLAUDE.md.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_locator.config import CodeLocatorConfig
from code_locator.indexing.index_builder import build_index
from code_locator.indexing.sqlite_store import SymbolDB
from code_locator.tools.validate_symbols import ValidateSymbolsTool
from code_locator_runtime import record_index_state


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _seed_repo(repo: Path) -> str:
    """Init a git repo with one Python file and commit. Returns HEAD SHA."""
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "module.py").write_text(
        "class CheckoutController:\n    def process_order(self):\n        return None\n"
    )
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "seed")
    return _git(repo, "rev-parse", "HEAD")


def test_validate_symbols_returns_indexed_at_sha(tmp_path: Path) -> None:
    """The sha returned by validate_symbols must equal git HEAD after a
    full build + record_index_state cycle — the production code path
    code_locator_runtime.rebuild_index uses.
    """
    repo = tmp_path / "repo"
    db_path = tmp_path / "code-graph.db"
    head_sha = _seed_repo(repo)

    build_index(str(repo), str(db_path))
    record_index_state(str(db_path), str(repo))

    db = SymbolDB(str(db_path))
    config = CodeLocatorConfig(sqlite_db=str(db_path))
    tool = ValidateSymbolsTool(db, config)

    results = tool.execute({"candidates": ["CheckoutController"]})
    db.close()

    assert results, "Expected at least one match for CheckoutController"
    for r in results:
        assert r.indexed_at_sha == head_sha, (
            f"indexed_at_sha={r.indexed_at_sha!r} did not match git HEAD={head_sha!r}"
        )
        # repo_path is recorded as the resolved path; compare against the
        # same resolution to avoid macOS /private/var vs /var aliasing.
        assert r.indexed_at_path == str(repo.resolve())


def test_validate_symbols_returns_empty_sha_when_meta_missing(
    tmp_path: Path,
) -> None:
    """When build_index runs without record_index_state (legacy index, or
    an in-progress first build), validate_symbols returns indexed_at_sha=""
    instead of raising. Caller-LLM treats empty as "snapshot unknown."
    """
    repo = tmp_path / "repo"
    db_path = tmp_path / "code-graph.db"
    _seed_repo(repo)

    build_index(str(repo), str(db_path))
    # Deliberately skip record_index_state — the index_meta table never
    # gets populated. read_index_meta should swallow the OperationalError
    # / missing-row path and return "".

    db = SymbolDB(str(db_path))
    config = CodeLocatorConfig(sqlite_db=str(db_path))
    tool = ValidateSymbolsTool(db, config)

    results = tool.execute({"candidates": ["CheckoutController"]})
    db.close()

    assert results, "Expected at least one match"
    for r in results:
        assert r.indexed_at_sha == ""
        assert r.indexed_at_path == ""


def test_validate_symbols_sha_cached_at_init(tmp_path: Path) -> None:
    """The sha is read once at tool init and reused. A second git commit
    landing AFTER init does not change the cached value — matches the
    existing symbol-list caching contract ("index doesn't change mid-run").
    """
    repo = tmp_path / "repo"
    db_path = tmp_path / "code-graph.db"
    first_sha = _seed_repo(repo)

    build_index(str(repo), str(db_path))
    record_index_state(str(db_path), str(repo))

    db = SymbolDB(str(db_path))
    config = CodeLocatorConfig(sqlite_db=str(db_path))
    tool = ValidateSymbolsTool(db, config)

    # Add a second commit on top — the tool was initialized against first_sha.
    (repo / "module.py").write_text(
        "class CheckoutController:\n"
        "    def process_order(self):\n"
        "        return None\n"
        "    def new_method(self):\n"
        "        return None\n"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "second")
    second_sha = _git(repo, "rev-parse", "HEAD")
    assert first_sha != second_sha

    results = tool.execute({"candidates": ["CheckoutController"]})
    db.close()

    for r in results:
        # Cached at init — reflects the build-time sha, not the post-commit one.
        assert r.indexed_at_sha == first_sha
