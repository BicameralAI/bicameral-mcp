"""pilot/mcp-owned runtime helpers for the real Code Locator integration.

Git-state tracking, stale-index recovery, and index rebuild orchestration
for the code locator package (code_locator/).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_INDEX_META_SQL = """\
CREATE TABLE IF NOT EXISTS index_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
)
"""


@dataclass(frozen=True)
class RepoIndexState:
    repo_path: str
    head_commit: str
    branch: str


def _default_cache_root() -> Path:
    """Writable repo-level cache root for MCP-owned Code Locator state."""
    repo_root = os.getenv("REPO_PATH")
    if repo_root:
        root = Path(repo_root).resolve() / ".bicameral"
    else:
        root = Path(__file__).resolve().parents[2] / ".bicameral"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_runtime_env() -> None:
    """Provide repo-local defaults so the MCP server works without home-dir writes."""
    cache_root = _default_cache_root()
    os.environ.setdefault("CODE_LOCATOR_SQLITE_DB", str(cache_root / "code-graph.db"))




def _git_stdout(repo_path: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def get_repo_index_state(repo_path: str) -> RepoIndexState:
    repo = str(Path(repo_path).resolve())
    branch = _git_stdout(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        branch = "DETACHED"
    return RepoIndexState(
        repo_path=repo,
        head_commit=_git_stdout(repo, "rev-parse", "HEAD"),
        branch=branch,
    )


def detect_authoritative_ref(repo_path: str) -> str:
    """Detect the repo's main branch name.

    Resolution order:
      1. ``BICAMERAL_AUTHORITATIVE_REF`` env var (explicit override — wins)
      2. ``git symbolic-ref refs/remotes/origin/HEAD`` (reads the remote
         default branch — the standard answer)
      3. Fallback to ``main``

    Used by ``BicameralContext`` to pin ledger baselines to the authoritative
    ref, so a user checked out on a feature branch can't accidentally poison
    the ledger.
    """
    explicit = os.environ.get("BICAMERAL_AUTHORITATIVE_REF", "").strip()
    if explicit:
        return explicit

    symref = _git_stdout(repo_path, "symbolic-ref", "refs/remotes/origin/HEAD")
    if symref:
        # Output format: refs/remotes/origin/main
        branch = symref.rsplit("/", 1)[-1]
        if branch:
            return branch

    return "main"


def resolve_ref_sha(repo_path: str, ref: str) -> str:
    """Resolve a git ref (branch name, tag, or SHA) to its commit SHA.

    Returns an empty string if the ref doesn't resolve (shallow clone,
    missing remote, typo). Callers should gracefully degrade rather than
    hard-fail — the pollution guard only engages when we can confirm
    HEAD ≠ authoritative, so an empty authoritative_sha means "can't tell,
    assume authoritative."
    """
    if not ref:
        return ""
    # Try the ref as-is first (main, a tag, a SHA).
    sha = _git_stdout(repo_path, "rev-parse", ref)
    if sha:
        return sha
    # Try origin/<ref> as a fallback — branches that exist on the remote
    # but not locally are the common case in CI and fresh clones.
    return _git_stdout(repo_path, "rev-parse", f"origin/{ref}")


def _connect_meta(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_INDEX_META_SQL)
    return conn


def record_index_state(db_path: str, repo_path: str) -> RepoIndexState:
    state = get_repo_index_state(repo_path)
    conn = _connect_meta(db_path)
    conn.executemany(
        """INSERT INTO index_meta (key, value)
           VALUES (?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        [
            ("repo_path", state.repo_path),
            ("head_commit", state.head_commit),
            ("branch", state.branch),
        ],
    )
    conn.commit()
    conn.close()
    return state


def _get_meta(db_path: str, key: str) -> str:
    conn = _connect_meta(db_path)
    row = conn.execute("SELECT value FROM index_meta WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else ""


def _symbol_count(db_path: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _indexed_file_count(db_path: str) -> int:
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _clear_legacy_index_tables(db_path: str) -> None:
    conn = _connect_meta(db_path)
    for table in ("edges", "symbols", "indexed_files"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            continue
    conn.commit()
    conn.close()


def rebuild_index(repo_path: str, config, force: bool = False) -> None:
    """Rebuild the underlying code locator index and persist git metadata."""
    from code_locator.indexing.sqlite_store import SymbolDB
    from code_locator.retrieval.bm25s_client import Bm25sClient

    repo = str(Path(repo_path).resolve())
    index_dir = str(Path(config.sqlite_db).parent)

    if config.indexing_backend == "cocoindex":
        from code_locator.indexing.cocoindex_pipeline import run_pipeline, sync_symbols_in_db
        from code_locator.indexing.graph_builder import build_graph

        run_pipeline(
            repo,
            config.sqlite_db,
            config.embedding_model,
            chunk_size=config.chunk_size,
            chunk_overlap=config.chunk_overlap,
        )
        sync_symbols_in_db(config.sqlite_db)
        db = SymbolDB(config.sqlite_db)
        build_graph(db, repo)
        db.close()
    else:
        from code_locator.indexing.index_builder import build_index

        if force:
            _clear_legacy_index_tables(config.sqlite_db)
        build_index(repo, config.sqlite_db)

    from code_locator.indexing.sqlite_store import SymbolDB as _SymDB
    _sdb = _SymDB(config.sqlite_db)
    bm25 = Bm25sClient()
    bm25.index(repo, index_dir, symbol_db=_sdb)
    _sdb.close()
    record_index_state(config.sqlite_db, repo)

    count = _symbol_count(config.sqlite_db)
    if count == 0:
        logger.error(
            "[mcp] build_index completed with 0 symbols for %s — "
            "tree-sitter language packages may not be installed correctly. "
            "Run: pip install 'bicameral-mcp[tree-sitter]' or reinstall via pipx.",
            repo,
        )


def ensure_index_matches_repo(repo_path: str, config) -> bool:
    """Refresh or bootstrap the local index when needed.

    Three cases handled:
    - Cold start (0 symbols, 0 files): first run, build from scratch.
    - Poisoned index (0 symbols, files > 0): prior run recorded files but
      extracted nothing — clear and rebuild.
    - Stale index (symbols exist, HEAD mismatch): refresh for new commit.
    """
    if _symbol_count(config.sqlite_db) == 0:
        if _indexed_file_count(config.sqlite_db) > 0:
            # Poisoned index — recorded files but 0 symbols extracted.
            logger.warning(
                "[mcp] poisoned index detected (%d files, 0 symbols) — clearing and rebuilding",
                _indexed_file_count(config.sqlite_db),
            )
            rebuild_index(repo_path, config, force=True)
        else:
            # Cold start — never indexed.
            logger.info("[mcp] cold start: building index for %s", repo_path)
            rebuild_index(repo_path, config)
        return True

    current = get_repo_index_state(repo_path)
    indexed_repo = _get_meta(config.sqlite_db, "repo_path")
    indexed_head = _get_meta(config.sqlite_db, "head_commit")
    bm25_path = Path(config.sqlite_db).parent / "bm25_index.pkl"

    refresh_reason = ""
    if not indexed_repo:
        refresh_reason = "missing_repo_metadata"
    elif str(Path(indexed_repo).resolve()) != current.repo_path:
        refresh_reason = "repo_changed"
    elif current.head_commit and indexed_head != current.head_commit:
        refresh_reason = "head_commit_changed"
    elif not bm25_path.exists():
        refresh_reason = "missing_bm25"

    if not refresh_reason:
        return False

    logger.info(
        "[mcp] refreshing code locator index for %s (%s)",
        current.repo_path,
        refresh_reason,
    )
    rebuild_index(repo_path, config, force=True)
    return True
