"""validate_symbols tool — fuzzy-match candidate names against the real symbol index."""

from __future__ import annotations

from rapidfuzz import fuzz

from ..config import CodeLocatorConfig
from ..indexing.sqlite_store import SymbolDB
from ..models import ValidatedSymbol

# JSON Schema for tool parameter validation
TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "validate_symbols",
        "description": "Validate candidate symbol names against the real codebase index. Returns matched symbols with fuzzy match scores. Use this to check if symbols you think exist actually exist in the codebase.",
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Symbol name hypotheses to validate (e.g. ['CheckoutController', 'processOrder', 'rate_limiter'])",
                }
            },
            "required": ["candidates"],
        },
    },
}


class ValidateSymbolsTool:
    """Validates candidate symbol names against the codebase index.

    Symbol list is cached at init time — index doesn't change mid-run.
    """

    def __init__(self, db: SymbolDB, config: CodeLocatorConfig) -> None:
        self.config = config
        # Retained so code_locator.adapter.ground_mappings() can reach
        # db.lookup_by_file() during auto-grounding. See adapters/code_locator.py:190.
        self._db = db
        # Cache symbol list at init (not per-call)
        self._symbols: list[tuple[int, str, str]] = db.get_all_symbol_names()

    def execute(self, args: dict) -> list[ValidatedSymbol]:
        candidates = args.get("candidates", [])
        results: list[ValidatedSymbol] = []

        for candidate in candidates:
            if len(candidate) < self.config.min_candidate_length:
                continue
            matches = self._fuzzy_match(candidate)
            results.extend(matches)

        return results

    def _fuzzy_match(self, candidate: str) -> list[ValidatedSymbol]:
        """2-stage fuzzy search: fast filter -> precise re-rank."""
        threshold = self.config.fuzzy_threshold
        # Short candidates (2-3 chars) need stricter matching to avoid noise
        if len(candidate) <= 3:
            threshold = max(threshold, 95)
        max_matches = self.config.fuzzy_max_matches_per_candidate
        is_single_word = " " not in candidate and "." not in candidate
        candidate_lower = candidate.lower()

        # Stage 1: Fast filter with fuzz.ratio on all symbols -> top 100
        scored = []
        for sym_id, name, qualified_name in self._symbols:
            score = max(
                fuzz.ratio(candidate_lower, name.lower()),
                fuzz.ratio(candidate_lower, qualified_name.lower()),
            )
            if score > 30:  # loose pre-filter
                scored.append((sym_id, name, qualified_name, score))

        scored.sort(key=lambda x: x[3], reverse=True)
        survivors = scored[:100]

        _SCORERS = {
            "WRatio": fuzz.WRatio,
            "token_set_ratio": fuzz.token_set_ratio,
            "partial_ratio": fuzz.partial_ratio,
        }
        scorer = _SCORERS.get(self.config.fuzzy_scorer, fuzz.WRatio)

        # Stage 2: Precise re-rank with configurable scorer on survivors
        reranked = []
        for sym_id, name, qualified_name, _ in survivors:
            score = max(
                scorer(candidate_lower, name.lower()),
                scorer(candidate_lower, qualified_name.lower()),
            )

            # Single-word candidates: require substring containment
            # Normalize by stripping underscores for cross-convention matching
            # (camelCase "processOrder" -> "processorder" matches snake_case "process_order" -> "processorder")
            if is_single_word:
                c_norm = candidate_lower.replace("_", "")
                n_norm = name.lower().replace("_", "")
                q_norm = qualified_name.lower().replace("_", "")
                if c_norm not in n_norm and c_norm not in q_norm:
                    continue

            if score >= threshold:
                reranked.append((sym_id, name, qualified_name, score))

        reranked.sort(key=lambda x: x[3], reverse=True)

        # Cap at max_matches per candidate
        return [
            ValidatedSymbol(
                original_candidate=candidate,
                matched_symbol=qn,
                match_score=score,
                symbol_id=sid,
                bridge_method="rapidfuzz_validate",
            )
            for sid, name, qn, score in reranked[:max_matches]
        ]
