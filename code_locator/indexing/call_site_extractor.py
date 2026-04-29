"""Multi-language call-site extraction via tree-sitter.

Sibling of ``symbol_extractor.py`` (which extracts *definitions*); this
module extracts *call sites*. Used by Phase 4's drift classifier
(``codegenome.drift_classifier._signal_no_new_calls``) to detect whether
a code change introduces new function calls — a strong "semantic
change" signal.

Design notes
------------

- Reuses ``symbol_extractor._get_parser`` so we don't duplicate the
  parser-caching, legacy-vs-modern tree-sitter dispatch, or the
  language-package map.
- Per-language extraction lives in tiny ``_extract_<lang>_calls``
  helpers (one tree-sitter node-type per language). The set of node
  types is the only language-specific knowledge here; the visit
  pattern is identical across languages.
- Returns a ``set[str]`` of *called callable names* — last identifier
  in a member-access expression (e.g. ``obj.method()`` → ``method``).
  This matches the granularity the classifier needs (does the new
  body call functions the old body didn't?).
- Failure-isolated: parser unavailable, parse error, unknown language
  all return ``set()``. Caller treats empty as "no signal" and
  downgrades the ``no_new_calls`` weight.

Phase 4 (#61) — issue #61 weighted-score table:
  no_new_calls signal contributes 0.15 to the cosmetic-vs-semantic
  classification. ``new_calls ⊆ old_calls`` → 1.0 (no new calls; signal
  votes "cosmetic"); otherwise → 0.0.
"""

from __future__ import annotations

from .symbol_extractor import _LANG_PACKAGE_MAP, _get_parser, _node_text

# Per-language tree-sitter node types that represent a call/invocation.
# Each value is a tuple ``(call_node_type, callee_field_name)`` where
# ``callee_field_name`` is the field on the call node whose subtree
# names the callable.
_CALL_NODES: dict[str, tuple[str, str]] = {
    "python": ("call", "function"),
    "javascript": ("call_expression", "function"),
    "typescript": ("call_expression", "function"),
    "go": ("call_expression", "function"),
    "rust": ("call_expression", "function"),
    "java": ("method_invocation", "name"),
    "c_sharp": ("invocation_expression", "function"),
}


def _last_identifier(text: str) -> str:
    """Return the trailing identifier in a member-access expression.

    ``"obj.method"`` → ``"method"``;
    ``"pkg::Module::func"`` → ``"func"`` (Rust);
    ``"a.b.c.d"`` → ``"d"``;
    ``"plain"`` → ``"plain"``.

    Splits on the last ``.``, ``::``, or ``->`` separator. The result
    is what the classifier compares — call-set membership at the
    callable level, not the receiver level.
    """
    for sep in ("::", "->", "."):
        if sep in text:
            return text.rsplit(sep, 1)[-1].strip()
    return text.strip()


def _walk_calls(
    node,
    code: bytes,
    call_type: str,
    callee_field: str,
    out: set[str],
) -> None:
    """Depth-first traversal collecting callee names."""
    if node.type == call_type:
        callee = node.child_by_field_name(callee_field)
        if callee is not None:
            name = _last_identifier(_node_text(code, callee))
            if name:
                out.add(name)
    for child in node.children:
        _walk_calls(child, code, call_type, callee_field, out)


def extract_call_sites(content: str, language: str) -> set[str]:
    """Return the set of callable names invoked inside ``content``.

    ``language`` must be one of the keys of ``_LANG_PACKAGE_MAP``
    (matches ``code_locator.indexing.symbol_extractor`` exactly —
    ``c_sharp`` with underscore, NOT ``csharp``).

    Returns ``set()`` on:

    - Unsupported language (``language`` not in the supported set).
    - Tree-sitter parser unavailable for the language at runtime.
    - Parse failure on the content.

    The classifier downgrades to "unknown" (signal returns 0.5) when
    callers explicitly observe an empty set on non-empty input — but
    the differentiation between "real empty" (no calls) and "could
    not extract" is the caller's concern, not this function's.
    """
    if language not in _CALL_NODES:
        return set()
    if language not in _LANG_PACKAGE_MAP:
        return set()
    try:
        parser = _get_parser(language)
    except Exception:
        return set()
    code = content.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(code)
    except Exception:
        return set()
    call_type, callee_field = _CALL_NODES[language]
    calls: set[str] = set()
    _walk_calls(tree.root_node, code, call_type, callee_field, calls)
    return calls
