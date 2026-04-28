"""Content-hash based status derivation (Branch Problem — Approach 2).

Status is NEVER stored as a fact. It is re-derived at query time by comparing:
    sha256(git show <ref>:<file>[start_line:end_line])
    vs
    code_region.content_hash (stored baseline from last link_commit)

This makes Bicameral immune to rebase, squash, and cherry-pick.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_symbol_lines(
    file_path: str,
    symbol_name: str,
    repo_path: str,
    ref: str = "HEAD",
) -> tuple[int, int] | None:
    """Resolve a symbol's current line range by name via tree-sitter.

    Returns (start_line, end_line) 1-indexed if found, None if not.
    Falls back gracefully — if tree-sitter isn't available or the symbol
    isn't found, returns None and the caller uses stored line numbers.
    """
    # Get full file content (not a line range)
    abs_repo = Path(repo_path).resolve()
    if ref == "working_tree":
        abs_file = abs_repo / file_path
        if not abs_file.exists():
            return None
        try:
            content = abs_file.read_text(errors="replace")
        except OSError:
            return None
    else:
        try:
            result = subprocess.run(
                ["git", "show", f"{ref}:{file_path}"],
                cwd=abs_repo, capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                return None
            content = result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError):
            return None

    try:
        from code_locator.indexing.symbol_extractor import extract_symbols_from_content

        ext = Path(file_path).suffix
        lang_map = {
            ".py": "python", ".js": "javascript", ".jsx": "javascript",
            ".ts": "typescript", ".tsx": "typescript", ".java": "java",
            ".go": "go", ".rs": "rust", ".cs": "csharp",
        }
        lang = lang_map.get(ext)
        if lang is None:
            return None

        symbols = extract_symbols_from_content(content, lang, file_path)
        for sym in symbols:
            name = getattr(sym, "name", None) or (sym.get("name") if isinstance(sym, dict) else None)
            qname = getattr(sym, "qualified_name", None) or (sym.get("qualified_name") if isinstance(sym, dict) else None)
            sl = getattr(sym, "start_line", None) or (sym.get("start_line") if isinstance(sym, dict) else None)
            el = getattr(sym, "end_line", None) or (sym.get("end_line") if isinstance(sym, dict) else None)
            if name == symbol_name or qname == symbol_name:
                return (sl, el)

        # Try fuzzy: symbol_name might be unqualified
        bare = symbol_name.split(".")[-1] if "." in symbol_name else symbol_name
        for sym in symbols:
            name = getattr(sym, "name", None) or (sym.get("name") if isinstance(sym, dict) else None)
            sl = getattr(sym, "start_line", None) or (sym.get("start_line") if isinstance(sym, dict) else None)
            el = getattr(sym, "end_line", None) or (sym.get("end_line") if isinstance(sym, dict) else None)
            if name == bare:
                return (sl, el)

    except (ImportError, Exception) as e:
        logger.debug("[status] symbol resolution fallback: %s", e)

    return None


def hash_lines(content: str, start_line: int, end_line: int) -> str:
    """SHA-256 of the specific line range (1-indexed, inclusive).

    Normalizes whitespace before hashing to avoid false drift from:
    - Trailing whitespace changes
    - Tab/space conversion (auto-formatters)
    - Trailing newline differences
    """
    lines = content.splitlines()
    # Convert to 0-indexed
    start = max(0, start_line - 1)
    end = min(len(lines), end_line)
    # Normalize: strip trailing whitespace per line
    normalized = [line.rstrip() for line in lines[start:end]]
    region = "\n".join(normalized)
    return hashlib.sha256(region.encode()).hexdigest()


def get_git_content(
    file_path: str,
    start_line: int,
    end_line: int,
    repo_path: str,
    ref: str = "HEAD",
) -> str | None:
    """Extract content of file[start:end] at a given git ref.

    Returns None if the file/ref doesn't exist (symbol not yet committed).
    Uses disk read for working tree (ref='working_tree').
    """
    abs_repo = Path(repo_path).resolve()
    abs_file = abs_repo / file_path

    if ref == "working_tree":
        if not abs_file.exists():
            return None
        try:
            return abs_file.read_text(errors="replace")
        except OSError:
            return None

    try:
        result = subprocess.run(
            ["git", "show", f"{ref}:{file_path}"],
            cwd=abs_repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError):
        return None


def compute_content_hash(
    file_path: str,
    start_line: int,
    end_line: int,
    repo_path: str,
    ref: str = "HEAD",
) -> str | None:
    """Compute sha256 of file[start_line:end_line] at git ref.

    Returns None if the file doesn't exist at that ref (symbol not yet in code).
    """
    content = get_git_content(file_path, start_line, end_line, repo_path, ref)
    if content is None:
        return None
    # Validate line range (warn but still hash — shorter file = drift signal)
    line_count = len(content.splitlines())
    if start_line < 1 or end_line < start_line:
        logger.warning(
            "[status] Invalid range %d:%d for %s",
            start_line, end_line, file_path,
        )
        return None
    return hash_lines(content, start_line, end_line)


def derive_status(
    stored_hash: str,
    actual_hash: str | None,
    cached_verdict: dict | None = None,
) -> str:
    """Derive intent status from hash state + optional LLM compliance verdict.

    Cache-aware semantics (post-v3 schema; plan: 2026-04-20-ingest-time-verification):

    - ``stored_hash`` empty            → ``ungrounded`` (never indexed)
    - ``actual_hash`` is ``None``      → ``pending`` (symbol absent at ref)
    - ``cached_verdict`` is ``None``   → ``pending`` (code exists, no verified
                                         judgment for this content shape)
    - ``cached_verdict['verdict'] == 'compliant'``  → ``reflected``
    - otherwise                        → ``drifted`` (verdict says code does
                                         not implement the decision)

    Callers that haven't been refactored to look up the ``compliance_check``
    cache pass ``cached_verdict=None`` and see ``pending`` where they
    previously saw ``reflected`` / ``drifted``. That is intentional: under
    the new plan, REFLECTED status MUST be earned by a caller-LLM verdict.
    ``adapter.ingest_commit`` (the drift-sweep site) looks up the cache
    via ``get_compliance_verdict(...)`` and passes the result here.
    """
    if not stored_hash:
        return "ungrounded"
    if actual_hash is None:
        return "pending"
    if cached_verdict is None:
        return "pending"
    if cached_verdict.get("verdict") == "compliant":
        return "reflected"
    return "drifted"


def get_changed_files(commit_hash: str, repo_path: str) -> list[str]:
    """Return list of files changed in a commit (relative to repo root)."""
    try:
        result = subprocess.run(
            ["git", "show", commit_hash, "--name-only", "--format="],
            cwd=Path(repo_path).resolve(),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            logger.warning("[status] git show failed for %s: %s", commit_hash, result.stderr[:200])
            return []
        return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError) as e:
        logger.warning("[status] git show error: %s", e)
        return []


def get_changed_files_in_range(
    base_sha: str,
    head_sha: str,
    repo_path: str,
) -> list[str] | None:
    """Return files touched between ``base_sha`` and ``head_sha``.

    v0.4.11 (latent drift fix): when ``link_commit`` runs after a gap of
    multiple commits since the last sync, sweeping only ``HEAD --name-only``
    misses every file drifted by intermediate commits. This helper runs
    ``git diff --name-only base..head`` to enumerate the full set of files
    touched across the gap, so the drift sweep covers everything that
    needs re-checking.

    Returns:
        - ``list[str]`` of changed file paths (possibly empty when the
          two refs touch no different files)
        - ``None`` when the diff failed (force-push, shallow clone,
          unreachable base SHA, etc.) — caller should fall back to
          ``get_changed_files(head_sha, repo_path)`` for head-only scope.

    The ``None`` sentinel matters: empty list means "ran successfully,
    no files differ" while ``None`` means "the range is unreachable."
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_sha}..{head_sha}"],
            cwd=Path(repo_path).resolve(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning(
                "[status] git diff %s..%s failed: %s",
                base_sha[:8], head_sha[:8], result.stderr[:200],
            )
            return None
        return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError) as e:
        logger.warning("[status] git diff range error: %s", e)
        return None


def resolve_head(repo_path: str) -> str | None:
    """Return current HEAD SHA."""
    return resolve_ref("HEAD", repo_path)


def resolve_ref(ref: str, repo_path: str) -> str | None:
    """Return the full SHA for a git ref (HEAD, branch, tag, or short SHA).

    Returns ``None`` when the ref is unreachable (force-pushed branch
    gone, detached tag removed, shallow clone that doesn't include
    the base). Callers must treat ``None`` as "ran, unresolvable" —
    distinct from returning an SHA that happens to match something
    stale.

    Issue #67: ``repo_path=""`` (or any path that doesn't resolve to a
    valid directory) used to call ``Path("").resolve()`` which returned
    the process CWD. On POSIX that often happened to be a git repo, so
    the call appeared to "work" with garbage data; on Windows it
    crashed with ``NotADirectoryError`` from CreateProcess. We now
    short-circuit to ``None`` when the resolved path isn't a directory.
    """
    if not ref or not repo_path:
        return None
    try:
        resolved_cwd = Path(repo_path).resolve()
    except (OSError, RuntimeError):
        return None
    if not resolved_cwd.is_dir():
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            cwd=resolved_cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, NotADirectoryError):
        pass
    return None
