"""Code locator adapter — MCP-native code locator backed by real index.

Exposes validate_symbols, search_code, get_neighbors, and
extract_symbols as direct methods. Also provides ground_mappings()
and resolve_symbols() for auto-grounding decisions to code.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from code_locator_runtime import (
    ensure_index_matches_repo,
    ensure_runtime_env,
)

logger = logging.getLogger(__name__)


def get_code_locator():
    """Return the code locator adapter backed by a real indexed repo."""
    repo_path = os.getenv("REPO_PATH", ".")
    return RealCodeLocatorAdapter(repo_path=repo_path)


def ensure_code_graph_fresh(repo_path: str | None = None) -> None:
    """Ensure the code graph index exists and matches HEAD.

    Safe to call multiple times — only rebuilds if stale.
    Called automatically by tools that depend on the code graph.
    """
    repo = repo_path or os.getenv("REPO_PATH", ".")
    ensure_runtime_env()
    from code_locator.config import load_config
    config = load_config()
    ensure_index_matches_repo(repo, config)


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

    # ── Grounding methods (moved from handlers/ingest.py) ────────────

    _AUTO_GROUND_THRESHOLD = 0.5

    _STOP_WORDS = frozenset({
        "the", "and", "for", "that", "this", "with", "are", "from", "have",
        "will", "when", "then", "been", "also", "into", "about", "should",
        "must", "need", "each", "they", "their", "there", "which", "where",
        "what", "than", "some", "more", "such", "only", "very", "just",
        "like", "make", "made", "use", "used", "using", "after", "before",
    })

    def _regions_from_symbol_ids(self, symbol_ids: list[int], db, description: str) -> list[dict]:
        """Resolve a list of symbol IDs to code_region dicts."""
        regions = []
        seen: set[int] = set()
        for sid in symbol_ids:
            if sid in seen:
                continue
            seen.add(sid)
            row = db.lookup_by_id(sid)
            if row:
                regions.append({
                    "symbol": row["qualified_name"] or row["name"],
                    "file_path": row["file_path"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "type": row["type"],
                    "purpose": description,
                })
        return regions

    def ground_mappings(self, mappings: list[dict]) -> tuple[list[dict], int]:
        """Auto-ground mappings with no code_regions via BM25 + fuzzy matching.

        Two-stage approach:
        1. BM25 search on full description -> top file -> expand to symbols
        2. Fuzzy token matching: tokenise description -> rapidfuzz against
           symbol names -> direct symbol lookup

        Returns (resolved_mappings, grounding_deferred_count).
        """
        try:
            self._ensure_initialized()
            db = self._validate_tool._db
        except Exception as exc:
            logger.warning("[ground] auto-ground unavailable: %s", exc)
            deferred = sum(1 for m in mappings if not m.get("code_regions"))
            return mappings, deferred

        resolved = []
        for mapping in mappings:
            if mapping.get("code_regions"):
                resolved.append(mapping)
                continue

            description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
            if not description:
                resolved.append(mapping)
                continue

            code_regions = []

            # Stage 1: BM25 file search
            try:
                hits = self.search_code(description)
                top = next((h for h in hits if h.get("score", 0) >= self._AUTO_GROUND_THRESHOLD), None)
                if top:
                    file_symbols = db.lookup_by_file(top["file_path"])
                    tokens = [
                        w for w in re.findall(r"[a-zA-Z]{4,}", description)
                        if w.lower() not in self._STOP_WORDS
                    ]
                    if tokens:
                        validated = self.validate_symbols(tokens)
                        matched_ids = {v["symbol_id"] for v in validated if v.get("symbol_id")}
                    else:
                        matched_ids = set()
                    file_symbol_ids = {row["id"] for row in file_symbols}
                    relevant_ids = matched_ids & file_symbol_ids
                    ranked = sorted(
                        file_symbols,
                        key=lambda r: (r["id"] not in relevant_ids, r["start_line"]),
                    )
                    code_regions = [
                        {
                            "symbol": row["qualified_name"] or row["name"],
                            "file_path": row["file_path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "type": row["type"],
                            "purpose": description,
                        }
                        for row in ranked[:3]
                    ]
                    if code_regions:
                        logger.info(
                            "[ground] stage1 BM25 grounded '%s' -> %s (%d symbols, score=%.2f)",
                            description[:60], top["file_path"], len(code_regions), top["score"],
                        )
            except Exception as exc:
                logger.warning("[ground] BM25 search failed for '%s': %s", description[:60], exc)

            # Stage 2: fuzzy token matching
            if not code_regions:
                tokens = [
                    w for w in re.findall(r"[a-zA-Z]{4,}", description)
                    if w.lower() not in self._STOP_WORDS
                ]
                if tokens:
                    try:
                        validated = self.validate_symbols(tokens)
                        symbol_ids = [v["symbol_id"] for v in validated if v.get("symbol_id")]
                        code_regions = self._regions_from_symbol_ids(symbol_ids[:5], db, description)
                        if code_regions:
                            matched = [v["matched_symbol"] for v in validated[:3]]
                            logger.info(
                                "[ground] stage2 fuzzy grounded '%s' -> %s",
                                description[:60], matched,
                            )
                    except Exception as exc:
                        logger.warning("[ground] fuzzy token match failed: %s", exc)

            if code_regions:
                resolved.append({**mapping, "code_regions": code_regions})
            else:
                logger.debug("[ground] no grounding found for: %s", description[:60])
                resolved.append(mapping)

        return resolved, 0

    def resolve_symbols(self, payload: dict) -> dict:
        """For each mapping with symbols[] but no code_regions, look up symbol
        names in the code graph and populate code_regions."""
        mappings = payload.get("mappings")
        if not mappings:
            return payload

        needs_resolution = any(
            m.get("symbols") and not m.get("code_regions")
            for m in mappings
        )
        if not needs_resolution:
            return payload

        try:
            self._ensure_initialized()
            db = self._validate_tool._db
        except Exception as exc:
            logger.warning("[resolve_symbols] cannot open symbol DB: %s", exc)
            return payload

        resolved_mappings = []
        for mapping in mappings:
            symbol_names = mapping.get("symbols") or []
            code_regions = mapping.get("code_regions") or []

            if symbol_names and not code_regions:
                for name in symbol_names:
                    try:
                        rows = db.lookup_by_name(name)
                    except Exception as exc:
                        logger.warning("[resolve_symbols] lookup_by_name failed for '%s': %s", name, exc)
                        rows = []
                    for row in rows:
                        code_regions.append({
                            "symbol": row["qualified_name"] or row["name"],
                            "file_path": row["file_path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "type": row["type"],
                            "purpose": mapping.get("intent", ""),
                        })
                if code_regions:
                    mapping = {**mapping, "code_regions": code_regions}
                else:
                    logger.debug("[resolve_symbols] no symbols found in index for: %s", symbol_names)

            resolved_mappings.append(mapping)

        return {**payload, "mappings": resolved_mappings}
