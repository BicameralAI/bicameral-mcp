"""Ledger Locator — resolve where ledger and code-graph state live for a project.

Public API:
    resolve_ledger_url(repo_path=None) -> str
    resolve_code_graph_path(repo_path=None) -> Path
    project_id_for(repo_path=None) -> str
    project_dir_for(repo_path=None) -> Path

The locator is deterministic: same project, same paths, regardless of which
working tree you call it from. See `docs/architecture/ledger-locator.md`
for the rationale (#368).
"""

from __future__ import annotations

import os
from pathlib import Path

from ._origin_guard import ProjectIdCollisionError, assert_origin
from ._project_id import ProjectIdResolutionError, common_dir_for
from ._project_id import project_id_for as _hash_id

__all__ = [
    "ProjectIdCollisionError",
    "ProjectIdResolutionError",
    "STATE_ROOT",
    "project_dir_for",
    "project_id_for",
    "resolve_code_graph_path",
    "resolve_ledger_url",
    "resolve_operator_config_path",
]

STATE_ROOT = Path.home() / ".bicameral" / "projects"


def _resolve_repo(repo_path: Path | None) -> Path:
    return Path(repo_path) if repo_path is not None else Path.cwd()


def project_id_for(repo_path: Path | None = None) -> str:
    """Return the 16-char hex project id for the repo at `repo_path`.

    `repo_path` defaults to the current working directory. Raises
    `ProjectIdResolutionError` if the path is not inside a git work tree.
    """
    return _hash_id(_resolve_repo(repo_path))


def project_dir_for(repo_path: Path | None = None) -> Path:
    """Return the per-project state directory under `STATE_ROOT`."""
    return STATE_ROOT / project_id_for(repo_path)


def resolve_ledger_url(repo_path: Path | None = None) -> str:
    """Return the SurrealDB URL for this project's ledger.

    Resolution order:
        1. `SURREAL_URL` env-var (unconditional override).
        2. `surrealkv://<STATE_ROOT>/<project-id>/ledger.db`.

    Writes / verifies `<project_dir>/origin.txt` on the default path
    (skipped when the env override is in effect — the caller has taken
    explicit responsibility for the URL).
    """
    override = os.environ.get("SURREAL_URL")
    if override:
        return override

    repo = _resolve_repo(repo_path)
    common = common_dir_for(repo)  # raises ProjectIdResolutionError
    project_dir = STATE_ROOT / _hash_id(repo)
    assert_origin(project_dir, common)
    return f"surrealkv://{project_dir / 'ledger.db'}"


def resolve_code_graph_path(repo_path: Path | None = None) -> Path:
    """Return the on-disk path for this project's code-graph SQLite file.

    Resolution order:
        1. `CODE_LOCATOR_SQLITE_DB` env-var (unconditional override).
        2. `<STATE_ROOT>/<project-id>/code-graph.db`.

    Like `resolve_ledger_url`, writes / verifies `origin.txt` on the
    default path.
    """
    override = os.environ.get("CODE_LOCATOR_SQLITE_DB")
    if override:
        return Path(override)

    repo = _resolve_repo(repo_path)
    common = common_dir_for(repo)
    project_dir = STATE_ROOT / _hash_id(repo)
    assert_origin(project_dir, common)
    return project_dir / "code-graph.db"


def resolve_operator_config_path(repo_path: Path | None = None) -> Path:
    """Return the on-disk path for this project's per-operator config file.

    Per R4 amendment (decision:5nr66wvmapjpt58rrji8): per-operator keys
    (telemetry, channel, guided, signer_email_fallback,
    render_source_attribution, team.author, team.role, rate-limit knobs,
    query timeouts) live at `~/.bicameral/projects/<id>/operator.yaml`,
    not in the git-committed `<repo>/.bicameral/config.yaml`.

    Per-machine, project-scoped, shared across worktrees on the same
    machine. No env-var override — operator.yaml is the operator's own
    state, not user-overridable per call.

    Writes / verifies `origin.txt` on first resolution (same as
    `resolve_ledger_url` and `resolve_code_graph_path`).
    """
    repo = _resolve_repo(repo_path)
    common = common_dir_for(repo)
    project_dir = STATE_ROOT / _hash_id(repo)
    assert_origin(project_dir, common)
    return project_dir / "operator.yaml"
