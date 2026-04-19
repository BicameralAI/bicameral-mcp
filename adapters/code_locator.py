"""Code locator adapter — MCP-native code locator backed by real index.

Exposes validate_symbols, search_code, get_neighbors, and
extract_symbols as direct methods. Also provides ground_mappings()
and resolve_symbols() for auto-grounding decisions to code.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
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

        self._db = db
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

    # ── Grounding methods (moved from handlers/ingest.py) ────────────

    # Progressive broadening tiers for coverage loop.
    # Each tier: (max_files, fuzzy_threshold, max_symbols)
    _COVERAGE_TIERS = [
        (3, 75, 5),    # Tier 0: strict — top 3 files, high fuzzy, 5 symbols
        (5, 65, 8),    # Tier 1: relaxed — top 5 files, medium fuzzy, 8 symbols
        (7, 55, 10),   # Tier 2: broad — top 7 files, low fuzzy, 10 symbols
    ]

    _SYMBOL_TYPE_PRIORITY = {
        "class": 0, "interface": 1, "type_alias": 2,
        "function": 3, "method": 4, "variable": 5,
    }

    _STOP_WORDS = frozenset({
        "the", "and", "for", "that", "this", "with", "are", "from", "have",
        "will", "when", "then", "been", "also", "into", "about", "should",
        "must", "need", "each", "they", "their", "there", "which", "where",
        "what", "than", "some", "more", "such", "only", "very", "just",
        "like", "make", "made", "use", "used", "using", "after", "before",
    })

    _COMPOUND_RE = re.compile(r"[A-Za-z]\w*(?:[_.][A-Za-z]\w*)+")

    _IDENTIFIER_RE = re.compile(
        r'(?:[A-Z][a-z]+(?:[A-Z][a-zA-Z0-9]*)+)'
        r'|(?:[a-z]+[A-Z][a-zA-Z0-9]*)'
    )

    _KEYWORD_WORDS = frozenset({
        "void", "return", "returns", "throw", "throws", "error", "errors",
        "class", "object", "function", "method", "type", "types", "typed",
        "state", "status", "call", "calls", "event", "events",
        "model", "field", "fields", "value", "values",
        "create", "update", "delete", "remove", "convert",
        "pattern", "guard", "against", "handling", "missing",
        "tracking", "stores", "simple", "produces", "negative",
        "constraint", "relationship", "dedicated", "worker", "general",
        "confirmation", "block", "split", "option", "release", "cycle",
        "currently", "causes", "causing", "records", "operations",
        "existing", "format", "parallel", "running", "process",
        "changes", "service", "based", "access", "system", "systems",
        "query", "queries", "level", "filter", "struct", "warning",
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

    def _type_priority(self, sid: int, db) -> int:
        row = db.lookup_by_id(sid)
        return self._SYMBOL_TYPE_PRIORITY.get(row["type"], 3) if row else 3

    def _ground_single(
        self,
        description: str,
        db,
        max_files: int,
        fuzzy_threshold: int,
        max_symbols: int,
        hits: list[dict] | None = None,
        mapping_symbol_names: list[str] | None = None,
    ) -> list[dict]:
        """Attempt to ground a single description at the given threshold tier.

        Args:
            hits: Pre-computed BM25 search results (from ground_mappings).
                  Passed in to avoid re-running the same search per tier.

        Returns code_regions list (empty if no match found at this tier).
        """
        code_regions: list[dict] = []
        if hits is None:
            hits = []

        # Track 1: Identifier-like tokens (compound and camelCase names)
        compounds = [c for c in self._COMPOUND_RE.findall(description) if len(c) >= 4]
        identifiers = [c for c in self._IDENTIFIER_RE.findall(description) if len(c) >= 6]
        identifier_tokens = list(dict.fromkeys(compounds + identifiers))

        # Track 2: Domain words — single words that are plausible symbol names.
        # Filters out generic stop words AND programming keywords that cause
        # false matches (e.g. "void" → Void, "return" → Return).
        # Short capitalized words (3 chars, e.g. "App", "JWT") are included
        # as they often name classes/types.
        domain_words = [
            w for w in re.findall(r"[a-zA-Z]{4,}", description)
            if w.lower() not in self._STOP_WORDS
            and w.lower() not in self._KEYWORD_WORDS
            and not self._IDENTIFIER_RE.fullmatch(w)
            and not self._COMPOUND_RE.fullmatch(w)
        ]

        # Track 3: Case-form bigrams from adjacent NL words
        nl_words = [
            w for w in re.findall(r"[a-zA-Z]{3,}", description)
            if w.lower() not in self._STOP_WORDS
            and w.lower() not in self._KEYWORD_WORDS
            and not self._IDENTIFIER_RE.fullmatch(w)
            and not self._COMPOUND_RE.fullmatch(w)
        ]
        case_forms: list[str] = []
        for i in range(len(nl_words) - 1):
            pascal = nl_words[i].capitalize() + nl_words[i + 1].capitalize()
            snake = nl_words[i].lower() + "_" + nl_words[i + 1].lower()
            if len(pascal) >= 8:
                case_forms.extend([pascal, snake])

        tokens = list(dict.fromkeys(identifier_tokens + domain_words + case_forms))

        # Pre-compute fuzzy-validated symbol IDs once. These serve two
        # purposes below:
        #   1. Seed the graph channel in search_code() so RRF fusion
        #      (code_locator/fusion/rrf.py) actually activates. Without
        #      a seed the graph channel is skipped and the pipeline
        #      collapses to BM25-only.
        #   2. Rank symbols within each qualifying file (relevant ones
        #      come first).
        matched_scores: dict[int, float] = {}
        if tokens:
            try:
                validated = self._validate_with_threshold(tokens, fuzzy_threshold)
                for v in validated:
                    sid = v.get("symbol_id")
                    if sid:
                        matched_scores[sid] = max(matched_scores.get(sid, 0), v["match_score"])
            except Exception as exc:
                logger.debug("[ground] fuzzy validate failed for '%s': %s", description[:60], exc)

        if mapping_symbol_names:
            for name in mapping_symbol_names:
                try:
                    rows = db.lookup_by_name(name)
                    for row in rows:
                        matched_scores[row["id"]] = max(matched_scores.get(row["id"], 0), 100.0)
                except Exception as exc:
                    logger.debug("[ground] symbol name lookup failed for '%s': %s", name, exc)

        matched_ids = set(matched_scores)

        # Stage 1: fuzzy-symbol direct lookup.
        # When fuzzy matching found symbols, resolve them directly — this is
        # the highest-precision path since matched_scores already rank by
        # relevance.
        if matched_ids:
            try:
                score_ranked_ids = sorted(matched_scores, key=matched_scores.get, reverse=True)
                code_regions = self._regions_from_symbol_ids(
                    score_ranked_ids[:max_symbols], db, description,
                )
            except Exception as exc:
                logger.warning("[ground] fuzzy direct lookup failed: %s", exc)

        # Stage 2: file-level fused retrieval.
        # - When Stage 1 found symbols: enriches with additional files from
        #   BM25+graph that Stage 1 may have missed (cross-file features).
        # - When Stage 1 found nothing: primary retrieval path via BM25.
        if len(code_regions) < max_symbols:
            try:
                if matched_ids:
                    fused = self.search_code(description, symbol_ids=sorted(matched_ids))
                else:
                    fused = hits
            except Exception as exc:
                logger.debug("[ground] fused search failed for '%s': %s — falling back to BM25-only", description[:60], exc)
                fused = hits

            covered_files = {r["file_path"] for r in code_regions}
            qualifying_files: list[str] = []
            seen_files: set[str] = set(covered_files)
            for h in fused:
                if not matched_ids and h.get("score", 0) < 0.1:
                    continue
                fp = h.get("file_path", "")
                if fp and fp not in seen_files:
                    seen_files.add(fp)
                    qualifying_files.append(fp)
                if len(qualifying_files) >= max_files:
                    break

            remaining_budget = max_symbols - len(code_regions)
            if qualifying_files and remaining_budget > 0:
                per_file_budget = max(1, remaining_budget // len(qualifying_files))
                try:
                    for fp in qualifying_files:
                        file_symbols = db.lookup_by_file(fp)
                        if not file_symbols:
                            continue
                        file_symbol_ids = {row["id"] for row in file_symbols}
                        relevant_ids = matched_ids & file_symbol_ids
                        ranked = sorted(
                            file_symbols,
                            key=lambda r: (
                                r["id"] not in relevant_ids,
                                -matched_scores.get(r["id"], 0),
                                self._SYMBOL_TYPE_PRIORITY.get(r["type"], 3),
                            ),
                        )
                        for row in ranked[:per_file_budget]:
                            code_regions.append({
                                "symbol": row["qualified_name"] or row["name"],
                                "file_path": row["file_path"],
                                "start_line": row["start_line"],
                                "end_line": row["end_line"],
                                "type": row["type"],
                                "purpose": description,
                            })
                        if len(code_regions) >= max_symbols:
                            break
                except Exception as exc:
                    logger.warning("[ground] file-level retrieval failed for '%s': %s", description[:60], exc)
                code_regions = code_regions[:max_symbols]

        return code_regions

    def ground_mappings(self, mappings: list[dict]) -> tuple[list[dict], int]:
        """Auto-ground mappings with coverage loop.

        For each ungrounded mapping, tries progressively relaxed thresholds
        (strict -> relaxed -> broad) before giving up. Max 3 attempts.

        BM25 search is called once per mapping and cached — only the
        score threshold and fuzzy threshold change across tiers.

        Returns (resolved_mappings, grounding_deferred_count).
        """
        try:
            self._ensure_initialized()
            db = self._db
        except Exception as exc:
            logger.warning("[ground] auto-ground unavailable: %s", exc)
            deferred = sum(1 for m in mappings if not m.get("code_regions"))
            return mappings, deferred

        _TIER_LABELS = ["strict", "relaxed", "broad"]

        resolved = []
        tier_counts: Counter[int] = Counter()
        for mapping in mappings:
            if mapping.get("code_regions"):
                resolved.append(mapping)
                continue

            description = mapping.get("intent") or (mapping.get("span") or {}).get("text", "")
            if not description:
                resolved.append(mapping)
                continue

            # FC-1 guard: refuse to ground queries that degenerate to <2 corpus
            # tokens. Witnessed in Accountable (2026-04-13): "GitHub Discussions
            # vs Slack" left only ``slack`` after stopword filtering, BM25
            # ranked every slack-mentioning file, and the #2 hit was anchored
            # to ``log-error-to-slack/index.ts:getFeatureName`` by tiebreak.
            # Under-specified queries belong as ungrounded open questions.
            try:
                corpus_token_count = self._search_tool.bm25.count_corpus_tokens(description)
            except Exception as exc:
                logger.debug("[ground] FC-1 token count failed for '%s': %s", description[:60], exc)
                corpus_token_count = 2  # fail-open: do not block grounding on detector failure
            if corpus_token_count < 2:
                logger.info(
                    "[ground] FC-1 skip: %d corpus tokens in %r — leaving ungrounded",
                    corpus_token_count, description[:60],
                )
                resolved.append(mapping)
                continue

            # Run BM25 search once and reuse across tiers.
            try:
                hits = self.search_code(description)
            except Exception as exc:
                logger.warning("[ground] BM25 search failed for '%s': %s", description[:60], exc)
                hits = []

            code_regions: list[dict] = []
            tier_used = -1

            for tier_idx, (max_files, fuzzy_thresh, max_sym) in enumerate(self._COVERAGE_TIERS):
                code_regions = self._ground_single(
                    description, db, max_files, fuzzy_thresh, max_sym,
                    hits=hits,
                    mapping_symbol_names=mapping.get("symbols"),
                )
                if code_regions:
                    tier_used = tier_idx
                    break

            if code_regions:
                tier_label = _TIER_LABELS[tier_used]
                tier_counts[tier_used] += 1
                # Stamp grounding_tier on each region so it flows through
                # to relate_maps_to provenance via ingest_payload.
                for region in code_regions:
                    region["grounding_tier"] = tier_used
                logger.info(
                    "[ground] grounded '%s' at tier %d (%s) -> %d regions",
                    description[:60], tier_used, tier_label, len(code_regions),
                )
                resolved.append({**mapping, "code_regions": code_regions})
            else:
                logger.debug(
                    "[ground] no grounding found after %d tiers: %s",
                    len(self._COVERAGE_TIERS), description[:60],
                )
                resolved.append(mapping)

        # Batch summary
        total = len(mappings)
        grounded = sum(tier_counts.values())
        if total > 0:
            logger.info(
                "[ground] summary: %d/%d grounded (tier0=%d, tier1=%d, tier2=%d)",
                grounded, total,
                tier_counts[0], tier_counts[1], tier_counts[2],
            )

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
