"""Project-id derivation — sha256 over the absolute git common-dir path.

A "project" is one git object database. Worktrees share it; clones don't.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class ProjectIdResolutionError(RuntimeError):
    """Raised when we cannot derive a project id from the given path.

    Usually because the path is not inside a git work tree. The message
    names both the missing `.git/` and the env-var escape hatch so the
    operator can recover without reading source.
    """


def common_dir_for(repo_path: Path) -> Path:
    """Return the absolute path to the repo's shared git directory.

    For a primary working tree this is `<repo>/.git`. For a linked
    worktree (`git worktree add`), it's the primary's `.git/` — which is
    why worktrees of the same project share a project id.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise ProjectIdResolutionError(
            f"could not resolve .git/ for {repo_path}: bicameral currently supports git only; "
            "non-git VCSes are not yet implemented. To use bicameral with this repo, run from "
            "inside a git working tree, OR set SURREAL_URL and CODE_LOCATOR_SQLITE_DB "
            "explicitly to bypass the locator."
        ) from exc

    raw = result.stdout.strip()
    candidate = Path(raw) if Path(raw).is_absolute() else repo_path / raw
    return candidate.resolve()


def project_id_for(repo_path: Path) -> str:
    """Return the 16-char hex project id for the repo at `repo_path`."""
    common = common_dir_for(repo_path)
    return hashlib.sha256(str(common).encode()).hexdigest()[:16]
