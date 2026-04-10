"""Code locator adapter — MCP-native code locator backed by real index.

Exposes validate_symbols, search_code, get_neighbors, and
extract_symbols as direct methods. The host LLM orchestrates tool calls.
"""

from __future__ import annotations

import os
from pathlib import Path

from code_locator_runtime import (
    ensure_index_matches_repo,
    ensure_runtime_env,
)


def get_code_locator():
    """Return the code locator adapter backed by a real indexed repo."""
    repo_path = os.getenv("REPO_PATH", ".")
    return RealCodeLocatorAdapter(repo_path=repo_path)


# Alias for the CodeIntelligencePort factory (same implementation, named for the port)
get_code_intelligence = get_code_locator


class RealCodeLocatorAdapter:
    """MCP-native code locator — exposes raw tools without an LLM agent loop.

    validate_symbols() → fuzzy-match candidates against symbol index
    search_code()      → BM25 + graph + vector retrieval with RRF fusion
    get_neighbors()    → 1-hop structural graph traversal
    extract_symbols()  → tree-sitter symbol extraction (no index needed)
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._repo_path = str(Path(repo_path).resolve())
        self._initialized = False
        self._validate_tool = None
        self._search_tool = None
        self._neighbors_tool = None

    def _ensure_initialized(self) -> None:
        """Lazy init of SymbolDB, BM25, config, and tool instances."""
        if self._initialized:
            return

        ensure_runtime_env()
        from code_locator.config import load_config
        from code_locator.indexing.sqlite_store import SymbolDB
        from code_locator.retrieval.bm25s_client import Bm25sClient
        from code_locator.tools.get_neighbors import GetNeighborsTool
        from code_locator.tools.search_code import SearchCodeTool
        from code_locator.tools.validate_symbols import ValidateSymbolsTool

        config = load_config()
        ensure_index_matches_repo(self._repo_path, config)

        db = SymbolDB(config.sqlite_db)
        if db.symbol_count() == 0:
            db.close()
            raise RuntimeError(
                "Code locator index is empty. Run: python -m code_locator index <repo_path>"
            )

        index_dir = str(Path(config.sqlite_db).parent)
        bm25 = Bm25sClient()
        try:
            bm25.load(index_dir)
        except FileNotFoundError:
            db.close()
            raise RuntimeError(
                "BM25 index not found. Run: python -m code_locator index <repo_path>"
            )

        vector_client = None
        if config.indexing_backend == "cocoindex":
            from code_locator.retrieval.sqlite_vec_client import SqliteVecClient
            vector_client = SqliteVecClient(config.sqlite_db, config.embedding_model)

        self._validate_tool = ValidateSymbolsTool(db, config)
        self._search_tool = SearchCodeTool(bm25, db, config, vector_client=vector_client)
        self._neighbors_tool = GetNeighborsTool(db, config)
        self._initialized = True

    def validate_symbols(self, candidates: list[str]) -> list[dict]:
        """Fuzzy-match candidate symbol names against the codebase index."""
        self._ensure_initialized()
        results = self._validate_tool.execute({"candidates": candidates})
        return [r.model_dump() for r in results]

    def search_code(self, query: str, symbol_ids: list[int] | None = None) -> list[dict]:
        """BM25 + graph + vector search with RRF fusion."""
        self._ensure_initialized()
        args = {"query": query}
        if symbol_ids is not None:
            args["symbol_ids"] = symbol_ids
        results = self._search_tool.execute(args)
        return [r.model_dump() for r in results]

    def get_neighbors(self, symbol_id: int) -> list[dict]:
        """1-hop structural graph traversal around a symbol."""
        self._ensure_initialized()
        results = self._neighbors_tool.execute({"symbol_id": symbol_id})
        return [r.model_dump() for r in results]

    async def extract_symbols(self, file_path: str) -> list[dict]:
        """Extract symbols from a file via tree-sitter (no LLM)."""
        from code_locator.indexing.symbol_extractor import extract_symbols

        abs_path = str(Path(file_path).resolve())
        records = extract_symbols(abs_path, self._repo_path)

        symbols = []
        for rec in records:
            sym_type = rec.type
            if sym_type not in ("function", "class", "module", "file"):
                sym_type = "function"
            symbols.append({
                "name": rec.qualified_name or rec.name,
                "type": sym_type,
                "start_line": rec.start_line,
                "end_line": rec.end_line,
            })
        return symbols
