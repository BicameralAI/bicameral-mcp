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
        except (subprocess.TimeoutExpired, FileNotFoundError):
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
    except (subprocess.TimeoutExpired, FileNotFoundError):
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
) -> str:
    """Derive intent status from hash comparison.

    - actual_hash is None → symbol absent at this ref → 'pending'
    - stored_hash is empty → never been indexed → 'ungrounded'
    - actual_hash == stored_hash → code unchanged → 'reflected' (or last set status)
    - actual_hash != stored_hash → code changed since baseline → 'drifted'

    Note: 'reflected' vs 'drifted' from hash alone is best-effort.
    Phase 3 will add an LLM drift judge for semantic comparison.
    """
    if not stored_hash:
        return "ungrounded"
    if actual_hash is None:
        return "pending"
    if actual_hash == stored_hash:
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
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
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
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("[status] git diff range error: %s", e)
        return None


def resolve_head(repo_path: str) -> str | None:
    """Return current HEAD SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(repo_path).resolve(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
