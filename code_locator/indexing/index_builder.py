"""Index builder: walks a repo, extracts symbols, writes to SQLite.

Supports incremental indexing via file mtime comparison.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from .sqlite_store import SymbolDB
from .symbol_extractor import EXTENSION_LANGUAGE, SKIP_DIRS, extract_symbols


@dataclass
class IndexStats:
    files_scanned: int = 0
    files_indexed: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    symbols_extracted: int = 0
    edges_created: int = 0
    duration_seconds: float = 0.0


def iter_source_files(repo_path: str):
    """Yield (rel_path, abs_path) for all supported source files."""
    for root, dirs, files in os.walk(repo_path):
        # Filter skip dirs in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for filename in sorted(files):
            ext = Path(filename).suffix.lower()
            if ext not in EXTENSION_LANGUAGE:
                continue
            abs_path = os.path.join(root, filename)
            if os.path.islink(abs_path):
                continue
            rel_path = Path(abs_path).relative_to(repo_path).as_posix()
            yield rel_path, abs_path


def build_index(repo_path: str, db_path: str) -> IndexStats:
    """Build or incrementally update the symbol index for a repo.

    Args:
        repo_path: Absolute path to the repository root.
        db_path: Path to the SQLite database file.

    Returns:
        IndexStats with counts and timing.
    """
    start = time.monotonic()
    stats = IndexStats()

    repo_path = str(Path(repo_path).resolve())
    db = SymbolDB(db_path)
    db.init_db()

    # Track which files we see so we can detect deletions
    seen_files: set[str] = set()
    previously_indexed = db.get_all_indexed_files()

    for rel_path, abs_path in iter_source_files(repo_path):
        stats.files_scanned += 1
        seen_files.add(rel_path)

        current_mtime = os.path.getmtime(abs_path)
        stored_mtime = db.get_file_mtime(rel_path)
        stored_symbol_count = db.get_file_symbol_count(rel_path)

        if stored_mtime is not None and current_mtime == stored_mtime and stored_symbol_count > 0:
            stats.files_skipped += 1
            continue

        # File is new or modified — re-extract
        if stored_mtime is not None:
            db.delete_file_symbols(rel_path)

        symbols = extract_symbols(abs_path, repo_path)
        if symbols:
            db.insert_symbols_batch(symbols)

        db.upsert_file_record(rel_path, current_mtime, len(symbols))
        stats.files_indexed += 1
        stats.symbols_extracted += len(symbols)

    # Remove entries for files that no longer exist
    for old_file in previously_indexed - seen_files:
        db.delete_file_record(old_file)
        stats.files_deleted += 1

    # Build dependency graph edges
    from .graph_builder import build_graph

    stats.edges_created = build_graph(db, repo_path)

    db.close()
    stats.duration_seconds = round(time.monotonic() - start, 3)
    return stats
