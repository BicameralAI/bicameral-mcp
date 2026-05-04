"""V1 B1 — tree-sitter cosmetic-change classifier (strict whitelist).

``is_cosmetic_change(before, after, lang)`` returns ``True`` only when
two snippets differ by whitespace alone — intra-line horizontal whitespace
outside string literals, trailing whitespace, or blank lines between
statements. Anything else routes to L3 with no ``cosmetic_hint``:

* identifier renames (kwargs / reflection / ORM lookups / template names)
* trailing-comma additions (Python tuple semantics, JS edge cases)
* comment edits (``# type: ignore``, ``// @ts-ignore``, build tags)
* docstring edits (observable via ``__doc__``, JSDoc tooling)
* string-literal edits, import reorders, any AST node insertion / deletion

Read-path advisory ONLY — never mutates ``content_hash``, never gates
drift detection. The output is metadata for the eventual V2 caller-LLM
verdict prompt (``cosmetic_hint`` field on ``DriftEntry``). False
negatives — real cosmetic changes routed unbiased to L3 — are cheap;
false positives bias the L3 prompt toward "looks fine," exactly the
failure mode the strict whitelist prevents.

Strategy: parse both inputs with tree-sitter, build a recursive
``(node.type, leaf_bytes_or_children)`` signature for each tree, and
compare. Two trees with the same signature differ only by whitespace
between tokens — tree-sitter does not represent inter-token whitespace
as nodes, so any non-whitespace difference (a different identifier, a
different comment, a different number of statements in a block) shows
up either as a different leaf-byte payload or as a different node-type
sequence in the tuple. Either case returns ``False``.
"""

from __future__ import annotations

import logging
from typing import Any

from code_locator.indexing.symbol_extractor import LANGUAGE_FALLBACK, _get_parser

logger = logging.getLogger(__name__)


# Languages B1 actually classifies. Anything else returns False (fail-safe).
# Matches the set wired into code_locator/indexing/symbol_extractor.py so
# the cosmetic detector never silently diverges from the indexer.
SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {
        "python",
        "javascript",
        "typescript",
        "java",
        "go",
        "rust",
        "c_sharp",
        # via LANGUAGE_FALLBACK
        "jsx",
        "tsx",
    }
)


def is_cosmetic_change(before: str, after: str, lang: str) -> bool:
    """Return True only if ``before → after`` is provably semantics-preserving.

    Args:
        before: Pre-change source snippet (e.g. the bound region's stored
            baseline bytes).
        after: Post-change source snippet (e.g. the same region's bytes
            at the live working tree).
        lang: Language identifier — e.g. ``"python"``, ``"typescript"``,
            ``"jsx"``. Resolved through ``LANGUAGE_FALLBACK`` first
            (``jsx``/``tsx`` map to their parent languages).

    Returns:
        ``True`` only when the two snippets are syntactically identical
        modulo whitespace. ``False`` for unsupported languages, parse
        failures, parse-error trees, or any structural difference.
    """
    if before == after:
        return True

    normalized = lang.lower().strip()
    if normalized not in SUPPORTED_LANGUAGES:
        return False
    resolved = LANGUAGE_FALLBACK.get(normalized, normalized)

    # Single guarded block: parse + tree-error check + recursive signature
    # comparison all live under one try/except so the function obeys its
    # documented "fail-safe → False" contract even when ``_signature``
    # blows the recursion limit on a deeply nested AST.
    try:
        parser = _get_parser(resolved)
        before_bytes = before.encode("utf-8")
        after_bytes = after.encode("utf-8")
        tree_before = parser.parse(before_bytes)
        tree_after = parser.parse(after_bytes)
        # If either input doesn't parse cleanly, refuse to call it cosmetic.
        if tree_before.root_node.has_error or tree_after.root_node.has_error:
            return False
        return _signature(tree_before.root_node, before_bytes) == _signature(
            tree_after.root_node, after_bytes
        )
    except (Exception, RecursionError) as exc:
        logger.debug("[ast_diff] classifier failed for %s: %s", normalized, exc)
        return False


def _signature(node: Any, source: bytes) -> tuple:
    """Recursive ``(node.type, child_sigs | leaf_bytes)`` signature.

    For interior nodes, the signature is ``(type, tuple_of_child_sigs)``.
    For leaf nodes, the signature is ``(type, leaf_bytes)`` — which
    captures identifier text, keyword text, operator text, string
    contents, comment contents, and so on.

    Two signatures are equal iff the trees have identical node-type
    structure AND identical leaf bytes. Any other difference — a
    different identifier, an extra statement, an edited comment —
    produces a signature mismatch.
    """
    if node.child_count == 0:
        return (node.type, source[node.start_byte : node.end_byte])
    return (
        node.type,
        tuple(_signature(child, source) for child in node.children),
    )
