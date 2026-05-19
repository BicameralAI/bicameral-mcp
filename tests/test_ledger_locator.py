"""Unit tests for `ledger_locator` — #368 Phase 1.

The locator resolves where ledger and code-graph state live for a project.
Sociable: real git binary, real filesystem. No mocks of the unit under test.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Initialize a fresh git repo at tmp_path and return it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clear_locator_env(monkeypatch):
    """Each test starts with a clean env so an outer SURREAL_URL or
    CODE_LOCATOR_SQLITE_DB doesn't leak into the locator's resolution.
    """
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


def test_default_resolves_under_home_bicameral_projects(git_repo: Path) -> None:
    import ledger_locator

    url = ledger_locator.resolve_ledger_url(repo_path=git_repo)
    code_graph = ledger_locator.resolve_code_graph_path(repo_path=git_repo)

    home = Path(os.environ["HOME"])
    expected_prefix = home / ".bicameral" / "projects"

    assert url.startswith("surrealkv://"), url
    assert str(expected_prefix) in url
    assert url.endswith("/ledger.db")

    assert str(code_graph).startswith(str(expected_prefix))
    assert code_graph.name == "code-graph.db"

    # Same project — ledger and code-graph live side by side.
    assert code_graph.parent == Path(url[len("surrealkv://"):]).parent


def test_env_override_wins_for_ledger_only(git_repo: Path, monkeypatch) -> None:
    import ledger_locator

    monkeypatch.setenv("SURREAL_URL", "memory://")

    url = ledger_locator.resolve_ledger_url(repo_path=git_repo)
    code_graph = ledger_locator.resolve_code_graph_path(repo_path=git_repo)

    assert url == "memory://"
    # code-graph still on disk under the project dir.
    assert code_graph.name == "code-graph.db"
    assert "/.bicameral/projects/" in str(code_graph)


def test_env_override_wins_for_code_graph_only(git_repo: Path, tmp_path: Path, monkeypatch) -> None:
    import ledger_locator

    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("CODE_LOCATOR_SQLITE_DB", str(explicit))

    url = ledger_locator.resolve_ledger_url(repo_path=git_repo)
    code_graph = ledger_locator.resolve_code_graph_path(repo_path=git_repo)

    assert code_graph == explicit
    # Ledger still resolves to its default home-relative path.
    assert url.startswith("surrealkv://")
    assert "/.bicameral/projects/" in url


def test_two_worktrees_resolve_to_same_id(git_repo: Path, tmp_path: Path) -> None:
    import ledger_locator

    # Need an initial commit before `git worktree add`.
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=git_repo, check=True)
    worktree = tmp_path / "wt2"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(worktree)],
        cwd=git_repo,
        check=True,
    )

    primary_id = ledger_locator.project_id_for(repo_path=git_repo)
    secondary_id = ledger_locator.project_id_for(repo_path=worktree)

    assert primary_id == secondary_id
    assert len(primary_id) == 16
    assert all(c in "0123456789abcdef" for c in primary_id)


def test_separate_clones_resolve_to_different_ids(tmp_path: Path) -> None:
    import ledger_locator

    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    for r in (repo_a, repo_b):
        r.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=r, check=True)

    assert ledger_locator.project_id_for(repo_path=repo_a) != ledger_locator.project_id_for(
        repo_path=repo_b
    )


def test_non_git_directory_raises_actionable_error(tmp_path: Path) -> None:
    import ledger_locator

    with pytest.raises(ledger_locator.ProjectIdResolutionError) as exc:
        ledger_locator.resolve_ledger_url(repo_path=tmp_path)

    msg = str(exc.value)
    # Names the missing .git/ AND points at the env-var override.
    assert ".git" in msg
    assert "SURREAL_URL" in msg


def test_resolve_operator_config_path_under_project_dir(git_repo: Path) -> None:
    """R4 (decision:5nr66wvmapjpt58rrji8): per-operator config lives at
    `~/.bicameral/projects/<id>/operator.yaml` — sibling to ledger.db and
    code-graph.db. Per-machine, project-scoped, shared across worktrees on
    the same machine. No env-var override.
    """
    import ledger_locator

    operator_path = ledger_locator.resolve_operator_config_path(repo_path=git_repo)
    code_graph = ledger_locator.resolve_code_graph_path(repo_path=git_repo)

    # Lives under the same project dir as code-graph.db (one bag of state).
    assert operator_path.parent == code_graph.parent
    assert operator_path.name == "operator.yaml"

    home = Path(os.environ["HOME"])
    assert str(home / ".bicameral" / "projects") in str(operator_path)


def test_resolve_operator_config_path_stable_across_worktrees(
    git_repo: Path, tmp_path: Path
) -> None:
    """R4: same project + different worktrees → same operator.yaml path."""
    import ledger_locator

    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "init"], cwd=git_repo, check=True
    )
    worktree = tmp_path / "wt2"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(worktree)],
        cwd=git_repo,
        check=True,
    )

    primary_op = ledger_locator.resolve_operator_config_path(repo_path=git_repo)
    secondary_op = ledger_locator.resolve_operator_config_path(repo_path=worktree)
    assert primary_op == secondary_op
