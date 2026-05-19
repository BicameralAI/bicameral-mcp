"""Tests for the explicit VCS contract surfaced from `ledger_locator/_project_id.py`.

R4 amendment (#368, decision:6c20xahdyxk3suzav4pj): bicameral's git-only
assumption must be surfaced as an explicit error message when `git
rev-parse --git-common-dir` fails — not as an opaque
subprocess.CalledProcessError. Forces future ports to jj / sapling /
fossil to be a deliberate locator amendment rather than an accidental
success on a misclassified VCS.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_locator_env(monkeypatch):
    monkeypatch.delenv("SURREAL_URL", raising=False)
    monkeypatch.delenv("CODE_LOCATOR_SQLITE_DB", raising=False)
    monkeypatch.delenv("BICAMERAL_LOCATOR_ALLOW_COLLISION", raising=False)


def test_non_git_directory_raises_with_vcs_message(tmp_path: Path) -> None:
    """R4 (decision:6c20xahdyxk3suzav4pj): the error names the VCS
    assumption verbatim so an operator on jj / sapling / fossil knows
    why their tool fails, and what they'd need to amend to support it.
    """
    import ledger_locator

    with pytest.raises(ledger_locator.ProjectIdResolutionError) as exc:
        ledger_locator.resolve_ledger_url(repo_path=tmp_path)

    msg = str(exc.value)
    # The verbatim contract phrase the plan promises.
    assert "bicameral currently supports git only" in msg
    assert "non-git VCSes are not yet implemented" in msg
    # The actionable next step is also present.
    assert "git working tree" in msg


def test_common_dir_for_raises_same_message(tmp_path: Path) -> None:
    """The error surfaces from the lowest-level locator helper too,
    not only from `resolve_ledger_url`. Callers using `common_dir_for`
    directly (e.g. `project_id_for`) get the same explicit message.
    """
    from ledger_locator._project_id import (
        ProjectIdResolutionError,
        common_dir_for,
    )

    with pytest.raises(ProjectIdResolutionError) as exc:
        common_dir_for(tmp_path)

    msg = str(exc.value)
    assert "bicameral currently supports git only" in msg


def test_project_id_for_propagates_vcs_message(tmp_path: Path) -> None:
    """`project_id_for` calls `common_dir_for` internally — the VCS
    contract error must propagate without being swallowed or rewrapped.
    """
    import ledger_locator

    with pytest.raises(ledger_locator.ProjectIdResolutionError) as exc:
        ledger_locator.project_id_for(repo_path=tmp_path)

    assert "bicameral currently supports git only" in str(exc.value)
