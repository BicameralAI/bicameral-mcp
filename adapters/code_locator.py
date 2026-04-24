"""Code locator adapter — MCP-native code locator backed by real index.

Exposes validate_symbols, get_neighbors, extract_symbols, and resolve_symbols
as direct methods. The server no longer performs keyword or vector code
search — callers resolve code regions themselves and hand paths/symbols
to the server via bind and preflight.
"""

from __future__ import annotations

import logging
import os
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


class RealCodeLocatorAdapter:
    """MCP-native code locator — exposes deterministic primitives only.

    validate_symbols() → fuzzy-match candidates against symbol index
    get_neighbors()    → 1-hop structural graph traversal
    extract_symbols()  → tree-sitter symbol extraction (no index needed)
    resolve_symbols()  → symbol name → code region lookup for ingest
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._repo_path = str(Path(repo_path).resolve())
        self._initialized = False
        self._validate_tool = None
        self._neighbors_tool = None

    def _ensure_initialized(self) -> None:
        """Lazy init of SymbolDB, config, and tool instances."""
        if self._initialized:
            return

        ensure_runtime_env()
        from code_locator.config import load_config
        from code_locator.indexing.sqlite_store import SymbolDB
        from code_locator.tools.get_neighbors import GetNeighborsTool
        from code_locator.tools.validate_symbols import ValidateSymbolsTool

        config = load_config()
        ensure_index_matches_repo(self._repo_path, config)

        db = SymbolDB(config.sqlite_db)
        if db.symbol_count() == 0:
            db.close()
            raise RuntimeError(
                "Code locator index is empty. Run: python -m code_locator index <repo_path>"
            )

        self._db = db
        self._validate_tool = ValidateSymbolsTool(db, config)
        self._neighbors_tool = GetNeighborsTool(db, config)
        self._initialized = True

    def validate_symbols(self, candidates: list[str]) -> list[dict]:
        """Fuzzy-match candidate symbol names against the codebase index."""
        self._ensure_initialized()
        results = self._validate_tool.execute({"candidates": candidates})
        return [r.model_dump() for r in results]

    def _validate_with_threshold(self, candidates: list[str], threshold: int) -> list[dict]:
        """Fuzzy-match with a custom threshold (for coverage loop broadening)."""
        self._ensure_initialized()
        original = self._validate_tool.config.fuzzy_threshold
        try:
            self._validate_tool.config.fuzzy_threshold = threshold
            results = self._validate_tool.execute({"candidates": candidates})
            return [r.model_dump() for r in results]
        finally:
            self._validate_tool.config.fuzzy_threshold = original

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
            db = self._db
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
