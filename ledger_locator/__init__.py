"""Ledger Locator — resolve where ledger and code-graph state live for a project.

Public API:
    resolve_ledger_url(repo_path=None) -> str
    resolve_code_graph_path(repo_path=None) -> Path
    resolve_bm25_index_path(repo_path=None) -> Path
    resolve_watermark_path(repo_path=None) -> Path
    resolve_pending_transcripts_dir(repo_path=None) -> Path
    resolve_processed_transcripts_dir(repo_path=None) -> Path
    resolve_operator_config_path(repo_path=None) -> Path
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
    "resolve_bm25_index_path",
    "resolve_code_graph_path",
    "resolve_ledger_url",
    "resolve_operator_config_path",
    "resolve_pending_transcripts_dir",
    "resolve_processed_transcripts_dir",
    "resolve_watermark_path",
]

STATE_ROOT = Path.home() / ".bicameral" / "projects"


def _resolve_repo(repo_path: Path | None) -> Path:
    return Path(repo_path) if repo_path is not None else Path.cwd()


def _resolved_project_dir(repo_path: Path | None) -> Path:
    """Resolve the project dir + assert origin in one shot.

    Used by every `resolve_*` function that returns a path under the
    project state dir. Centralizes the resolve-then-verify pattern so the
    public API stays a thin layer on top of the (repo → project-id →
    origin-guard) pipeline.
    """
    repo = _resolve_repo(repo_path)
    common = common_dir_for(repo)  # raises ProjectIdResolutionError
    project_dir = STATE_ROOT / _hash_id(repo)
    assert_origin(project_dir, common)
    return project_dir


def project_id_for(repo_path: Path | None = None) -> str:
    """Return the 16-char hex project id for the repo at `repo_path`.

    `repo_path` defaults to the current working directory. Raises
    `ProjectIdResolutionError` if the path is not inside a git work tree.
    """
    return _hash_id(_resolve_repo(repo_path))


def project_dir_for(repo_path: Path | None = None) -> Path:
    """Return the per-project state directory under `STATE_ROOT`.

    Does NOT verify the origin guard (see `_resolved_project_dir` for the
    verifying variant used internally by every `resolve_*` function).
    """
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
    return f"surrealkv://{_resolved_project_dir(repo_path) / 'ledger.db'}"


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
    return _resolved_project_dir(repo_path) / "code-graph.db"


def resolve_bm25_index_path(repo_path: Path | None = None) -> Path:
    """Return the on-disk path for this project's BM25 token index.

    R3 addition (#368): the BM25 index is derived from the same symbol
    corpus as `code-graph.db` and must travel with it. Sibling under the
    project dir, no env-var override.
    """
    return _resolved_project_dir(repo_path) / "bm25_index.pkl"


def resolve_watermark_path(repo_path: Path | None = None) -> Path:
    """Return the on-disk path for this project's peer-event watermark.

    R3 addition (#368): replaces `events/materializer.py`'s
    `local_dir / "watermark"`. Per-peer event-replay offsets are
    project-scoped so worktrees do not re-replay peer JSONL N times.
    """
    return _resolved_project_dir(repo_path) / "watermark"


def resolve_pending_transcripts_dir(repo_path: Path | None = None) -> Path:
    """Return the on-disk dir for this project's pending-transcripts queue.

    R3 addition (#368): replaces `events/transcript_queue.py:_pending_root`.
    SessionEnd-hook queue is project-scoped so transcript ingested in
    worktree A is visible to worktree B's drain loop.
    """
    return _resolved_project_dir(repo_path) / "pending-transcripts"


def resolve_processed_transcripts_dir(repo_path: Path | None = None) -> Path:
    """Return the on-disk dir for this project's processed-transcripts archive.

    R3 addition (#368): sibling to pending-transcripts; also project-scoped.
    """
    return _resolved_project_dir(repo_path) / "processed-transcripts"


def resolve_operator_config_path(repo_path: Path | None = None) -> Path:
    """Return the on-disk path for this project's per-operator config file.

    R4 amendment (decision:5nr66wvmapjpt58rrji8): per-operator keys
    (telemetry, channel, guided, signer_email_fallback,
    render_source_attribution, team.author, team.role, rate-limit knobs,
    query timeouts) live at `~/.bicameral/projects/<id>/operator.yaml`,
    not in the git-committed `<repo>/.bicameral/config.yaml`.

    Per-machine, project-scoped, shared across worktrees on the same
    machine. No env-var override — operator.yaml is the operator's own
    state, not user-overridable per call.
    """
    return _resolved_project_dir(repo_path) / "operator.yaml"
