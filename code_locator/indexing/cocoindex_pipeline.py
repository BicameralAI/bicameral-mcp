"""CocoIndex-based indexing pipeline (Option A+).

Single-DB architecture: writes symbols into config.sqlite_db.
CocoIndex handles incrementality via fingerprinting; symbols are synced to
the legacy ``symbols`` table via a lightweight in-DB copy.

Requires: pip install cocoindex sentence-transformers

Uses cocoindex v0.3+ flow API:
  - @cocoindex.flow_def for pipeline definition
  - cocoindex.sources.LocalFile for file walking
  - cocoindex.functions.SplitRecursively / SentenceTransformerEmbed for chunking+embedding
  - Custom @cocoindex.op.function for symbol extraction
  - Postgres for CocoIndex internal state (COCOINDEX_DATABASE_URL)
"""

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .symbol_extractor import EXTENSION_LANGUAGE, SKIP_DIRS, extract_symbols_from_content

logger = logging.getLogger(__name__)

# ── Row schemas (pure Python — no cocoindex dependency) ────────────


@dataclass
class CodeChunk:
    """Row schema for the embeddings table."""

    id: int  # content-based hash for stable identity across runs
    file_path: str
    language: str
    content: str
    start_line: int
    end_line: int
    embedding: list[float]


@dataclass
class SymbolRow:
    """Row schema for the CocoIndex-managed symbols table."""

    id: int  # content-based hash for stable identity across runs
    name: str
    qualified_name: str
    type: str
    file_path: str
    start_line: int
    end_line: int
    signature: str
    parent_qualified_name: str


# ── Stable ID generation ────────────────────────────────────────────


def _stable_id(key: str) -> int:
    """Generate a stable integer ID from a string key.

    Uses a 63-bit hash (positive) so it fits in SQLite INTEGER.
    Same input always produces the same ID across runs, enabling
    CocoIndex's reconciliation to skip unchanged rows.
    """
    return int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)


# ── Helpers ─────────────────────────────────────────────────────────


def _ext_to_language(filename: str) -> str:
    """Map file extension to language identifier."""
    ext = Path(filename).suffix.lower()
    return EXTENSION_LANGUAGE.get(ext, "")


# ── File include patterns ───────────────────────────────────────────

_INCLUDE_PATTERNS = [f"**/*{ext}" for ext in EXTENSION_LANGUAGE]
_EXCLUDE_DIR_PATTERNS = [f"{d}/" for d in SKIP_DIRS]


# ── CocoIndex flow definition (deferred import) ─────────────────────


def _define_flow(
    repo_path: str,
    embedding_model: str,
    chunk_size: int,
    chunk_overlap: int,
):
    """Define the CocoIndex flow using v0.3 flow API.

    Returns the flow definition function. Imports cocoindex only when called.
    """
    import cocoindex

    @cocoindex.op.function()
    def ext_to_language(filename: str) -> str:
        return _ext_to_language(filename)

    @cocoindex.op.function()
    def extract_file_symbols(filename: str, content: str) -> list[dict]:
        """Extract symbols from file content via tree-sitter."""
        language = _ext_to_language(filename)
        if not language or not content.strip():
            return []
        symbols = extract_symbols_from_content(content, language, filename)
        return [
            {
                "id": _stable_id(f"sym:{filename}:{s.qualified_name}:{s.start_line}"),
                "name": s.name,
                "qualified_name": s.qualified_name,
                "type": s.type,
                "file_path": s.file_path,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "signature": s.signature,
                "parent_qualified_name": s.parent_qualified_name,
            }
            for s in symbols
        ]

    @cocoindex.transform_flow()
    def text_to_embedding(
        text: cocoindex.DataSlice[str],
    ) -> cocoindex.DataSlice[list[float]]:
        return text.transform(
            cocoindex.functions.SentenceTransformerEmbed(model=embedding_model)
        )

    @cocoindex.flow_def(name="CodeLocatorIndex")
    def code_locator_flow(
        flow_builder: cocoindex.FlowBuilder,
        data_scope: cocoindex.DataScope,
    ) -> None:
        data_scope["files"] = flow_builder.add_source(
            cocoindex.sources.LocalFile(
                path=repo_path,
                included_patterns=_INCLUDE_PATTERNS,
                excluded_patterns=_EXCLUDE_DIR_PATTERNS,
            )
        )

        # Collector for chunk embeddings
        chunk_collector = data_scope.add_collector()
        # Collector for symbols
        symbol_collector = data_scope.add_collector()

        with data_scope["files"].row() as file:
            file["language"] = file["filename"].transform(ext_to_language)

            # Path 1: Chunks + embeddings
            file["chunks"] = file["content"].transform(
                cocoindex.functions.SplitRecursively(),
                language=file["language"],
                chunk_size=chunk_size,
                min_chunk_size=max(50, chunk_size // 5),
                chunk_overlap=chunk_overlap,
            )

            with file["chunks"].row() as chunk:
                chunk["embedding"] = chunk["text"].call(text_to_embedding)
                chunk_collector.collect(
                    filename=file["filename"],
                    language=file["language"],
                    content=chunk["text"],
                    start=chunk["start"],
                    end=chunk["end"],
                    embedding=chunk["embedding"],
                )

            # Path 2: Symbol extraction
            file["symbols"] = file["content"].transform(
                extract_file_symbols, file["filename"]
            )

            with file["symbols"].row() as sym:
                symbol_collector.collect(
                    name=sym["name"],
                    qualified_name=sym["qualified_name"],
                    sym_type=sym["type"],
                    file_path=sym["file_path"],
                    start_line=sym["start_line"],
                    end_line=sym["end_line"],
                    signature=sym["signature"],
                    parent_qualified_name=sym["parent_qualified_name"],
                )

        # Export embeddings to Postgres (CocoIndex manages the table)
        chunk_collector.export(
            "code_embeddings",
            cocoindex.storages.Postgres(),
            primary_key_fields=["filename", "content"],
            vector_indexes=[
                cocoindex.VectorIndexDef(
                    field_name="embedding",
                    metric=cocoindex.VectorSimilarityMetric.COSINE_SIMILARITY,
                )
            ],
        )

        # Export symbols to Postgres
        symbol_collector.export(
            "cocoindex_symbols",
            cocoindex.storages.Postgres(),
            primary_key_fields=["qualified_name", "file_path", "start_line"],
        )

    return code_locator_flow, text_to_embedding


# ── Public API ──────────────────────────────────────────────────────


@dataclass
class PipelineStats:
    """Statistics from a pipeline run."""

    duration_seconds: float = 0.0
    symbols_extracted: int = 0
    chunks_created: int = 0


def run_pipeline(
    repo_path: str,
    db_path: str,
    embedding_model: str,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> PipelineStats:
    """Run the CocoIndex indexing pipeline.

    Writes embeddings and symbol records via CocoIndex's flow API.
    Requires COCOINDEX_DATABASE_URL to be set (Postgres for internal state).

    Args:
        repo_path: Absolute path to the repository to index.
        db_path: Path to the SQLite database (same as config.sqlite_db).
        embedding_model: SentenceTransformer model name or path.
        chunk_size: Target chunk size for text splitting.
        chunk_overlap: Overlap between adjacent chunks.

    Returns:
        PipelineStats with timing and counts.
    """
    import cocoindex

    start = time.monotonic()
    repo = str(Path(repo_path).resolve())

    cocoindex.init()

    flow_def, _ = _define_flow(repo, embedding_model, chunk_size, chunk_overlap)

    flow_def.setup()
    flow_def.update()

    # Count results — query CocoIndex's managed tables
    try:
        sym_count = _count_cocoindex_table("cocoindex_symbols")
    except Exception:
        sym_count = 0
    try:
        chunk_count = _count_cocoindex_table("code_embeddings")
    except Exception:
        chunk_count = 0

    stats = PipelineStats(
        duration_seconds=round(time.monotonic() - start, 3),
        symbols_extracted=sym_count,
        chunks_created=chunk_count,
    )

    logger.info(
        "[cocoindex] pipeline complete: %d chunks, %d symbols in %.1fs",
        stats.chunks_created,
        stats.symbols_extracted,
        stats.duration_seconds,
    )

    return stats


def _count_cocoindex_table(table_name: str) -> int:
    """Count rows in a CocoIndex-managed Postgres table.

    Falls back to 0 if the table doesn't exist or connection fails.
    """
    import os
    try:
        import psycopg2
        url = os.environ.get("COCOINDEX_DATABASE_URL", "")
        if not url:
            return 0
        conn = psycopg2.connect(url)
        cur = conn.cursor()
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")  # noqa: S608
        count = cur.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0


def sync_symbols_in_db(db_path: str) -> int:
    """Sync symbols from cocoindex_symbols → legacy symbols table (same DB).

    Lightweight in-DB copy: DELETE + INSERT SELECT within one connection.
    Returns the number of symbols synced.
    """
    from .sqlite_store import SymbolDB

    # Ensure legacy tables exist
    db = SymbolDB(db_path)
    db.init_db()
    db.close()

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM cocoindex_symbols").fetchone()[0]
    except sqlite3.OperationalError:
        logger.warning("[cocoindex] cocoindex_symbols table not found in %s", db_path)
        conn.close()
        return 0

    # In-DB copy: clear legacy table, insert from CocoIndex table
    conn.execute("DELETE FROM symbols")
    conn.execute("DELETE FROM indexed_files")
    conn.execute("""
        INSERT INTO symbols (name, qualified_name, type, file_path,
                             start_line, end_line, signature, parent_qualified_name)
        SELECT name, qualified_name, type, file_path,
               start_line, end_line, signature, parent_qualified_name
        FROM cocoindex_symbols
    """)

    # Build indexed_files from symbol data
    conn.execute("""
        INSERT OR REPLACE INTO indexed_files (file_path, mtime, symbol_count)
        SELECT file_path, 0.0, COUNT(*)
        FROM cocoindex_symbols
        GROUP BY file_path
    """)
    conn.commit()
    conn.close()

    return count
