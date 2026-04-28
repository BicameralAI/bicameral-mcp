"""Regression tests for issue #67 — subprocess.run(cwd=...) safety.

Issue #67: subprocess wrappers (``resolve_ref`` in ledger/status.py,
``_git_stdout`` in code_locator_runtime.py) called
``subprocess.run(..., cwd=Path(repo_path).resolve())`` without
validating that ``repo_path`` was non-empty or pointed at an existing
directory.

POSIX silently degraded to the test runner's CWD (often a git repo, so
the call appeared to "work" with garbage data — a different bug class).
Windows raised ``NotADirectoryError [WinError 267]`` from
``CreateProcess``, which wasn't in the wrappers' ``except`` tuples,
crashing the entire test session.

This file pins the contract:

  - empty / missing ``repo_path``  → returns the wrapper's "unresolved"
    value (``None`` for resolve_ref, ``""`` for _git_stdout)
  - non-existent path              → ditto
  - path pointing at a file        → ditto
  - valid directory                → normal behaviour

The fix also adds ``NotADirectoryError`` to the ``except`` tuples in
the other subprocess sites (``get_git_content``,
``get_changed_files``, etc.) so an unexpected bad-cwd never escalates.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_locator_runtime import _git_stdout
from ledger.status import resolve_ref


class TestResolveRefHandlesBadRepoPath:
    """``resolve_ref`` returns ``None`` instead of crashing on bad inputs."""

    def test_empty_repo_path_returns_none(self) -> None:
        assert resolve_ref("HEAD", "") is None

    def test_nonexistent_repo_path_returns_none(self, tmp_path: Path) -> None:
        # Construct a path that explicitly does not exist.
        bogus = tmp_path / "definitely-does-not-exist"
        assert not bogus.exists()
        assert resolve_ref("HEAD", str(bogus)) is None

    def test_repo_path_points_at_a_file_returns_none(self, tmp_path: Path) -> None:
        f = tmp_path / "i-am-a-file.txt"
        f.write_text("hello")
        assert f.is_file() and not f.is_dir()
        assert resolve_ref("HEAD", str(f)) is None

    def test_empty_ref_returns_none(self, tmp_path: Path) -> None:
        # A valid repo_path but an empty ref must also short-circuit.
        assert resolve_ref("", str(tmp_path)) is None


class TestGitStdoutHandlesBadRepoPath:
    """``_git_stdout`` returns ``""`` instead of crashing on bad inputs."""

    def test_empty_repo_path_returns_empty(self) -> None:
        assert _git_stdout("", "rev-parse", "HEAD") == ""

    def test_nonexistent_repo_path_returns_empty(self, tmp_path: Path) -> None:
        bogus = tmp_path / "definitely-does-not-exist"
        assert not bogus.exists()
        assert _git_stdout(str(bogus), "rev-parse", "HEAD") == ""

    def test_repo_path_points_at_a_file_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "i-am-a-file.txt"
        f.write_text("hello")
        assert _git_stdout(str(f), "rev-parse", "HEAD") == ""


class TestResolveRefStillWorksOnValidRepo:
    """Sanity: a real git repo still resolves HEAD correctly."""

    def test_returns_sha_for_real_head(self, tmp_path: Path) -> None:
        repo = tmp_path / "real-repo"
        repo.mkdir()
        # Set up a minimal git repo with one commit.
        for cmd in [
            ["git", "init", "-q", "-b", "main"],
            ["git", "config", "user.email", "test@example.com"],
            ["git", "config", "user.name", "Test"],
        ]:
            subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
        (repo / "x.txt").write_text("hi")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
            cwd=repo, check=True, capture_output=True,
        )

        sha = resolve_ref("HEAD", str(repo))
        assert sha is not None
        assert len(sha) == 40  # SHA-1 hex


class TestNotADirectoryErrorInExceptClauses:
    """Pin the source-level invariant: every subprocess.run wrapper that
    accepts a user-supplied ``repo_path`` must catch NotADirectoryError.

    This is a static check — if a future commit removes
    ``NotADirectoryError`` from one of these except clauses, the test
    fails by re-introducing the original Windows crash class.
    """

    @pytest.mark.parametrize("module_path", [
        "ledger/status.py",
        "ledger/adapter.py",
        "code_locator_runtime.py",
    ])
    def test_subprocess_except_includes_notadirectoryerror(
        self, module_path: str
    ) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        source = (repo_root / module_path).read_text(encoding="utf-8")
        # Permit modules with no subprocess.run at all.
        if "subprocess.run" not in source:
            pytest.skip(f"{module_path} has no subprocess.run")
        # Find every "except (subprocess.TimeoutExpired, ...)" block; each
        # must include NotADirectoryError so a bad cwd is handled gracefully.
        # We accept either the exact tuple form or any except clause that
        # mentions NotADirectoryError near a subprocess.run.
        # Coarse but effective: count occurrences and require parity.
        timeout_exp_excepts = source.count("except (subprocess.TimeoutExpired,")
        nadir_excepts = source.count("NotADirectoryError")
        # Some files have one subprocess.run guarded by a broader
        # ``except Exception`` — that's also acceptable. Only enforce the
        # parity rule when the file uses the typed-tuple form at all.
        if timeout_exp_excepts > 0:
            assert nadir_excepts >= timeout_exp_excepts, (
                f"{module_path}: every "
                f"`except (subprocess.TimeoutExpired, ...)` must include "
                f"NotADirectoryError to avoid Windows WinError 267 (#67). "
                f"Found {timeout_exp_excepts} typed-tuple excepts but only "
                f"{nadir_excepts} mentions of NotADirectoryError."
            )
