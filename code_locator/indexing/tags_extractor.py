"""Generic tree-sitter tags.scm symbol extractor (#367, substrate for #399).

Loads any language's ``queries/tags.scm`` (from the installed grammar
package or a vendored override) and emits :class:`SymbolRecord` objects
for every ``@definition.<kind>`` capture. Capture kinds map to the
existing walker vocabulary via :data:`_KIND_TO_TYPE` so downstream
consumers (``validate_symbols``, the bind handler) treat tags-extracted
symbols the same as walker-extracted ones.

The substrate is **purely data-driven**: every supported language goes
through one function. Per-language walkers stay only where the tags
schema cannot express what we need (currently: ``_extract_csharp_defs``
for nested-class qualified-name tracking, ``_extract_java_imports`` for
annotation/wildcard handling). Walker retirement for the remaining
languages is gated on :issue:`399`'s measurement spike; this PR only
introduces the substrate, validated by ``tests/test_tags_extractor_parity.py``.

Design decisions locked at scoping time (see #367 body):
- ``@definition.module`` → ``"class"`` (both define a containing scope).
- ``@definition.method`` → ``"function"`` (walker treats them interchangeably).
- ``parent_qualified_name`` computed via ancestor walk to the nearest
  enclosing ``@definition.*`` capture, so nested cases (e.g. a method
  inside a class, an Elixir function inside ``defmodule``) produce
  correct qualified names like ``ClassName.method_name`` or
  ``MyApp.Accounts.get_user``.
"""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path

import tree_sitter as ts

from .sqlite_store import SymbolRecord

# Map tags.scm ``@definition.<kind>`` → ``SymbolRecord.type`` vocabulary.
# Kinds not in this map are filtered out — most notably ``constant`` (which
# tags.scm emits for module-level assignments but our walkers explicitly
# skip) and any future kinds we don't yet support downstream.
_KIND_TO_TYPE: dict[str, str] = {
    "module": "class",
    "class": "class",
    "interface": "class",
    "function": "function",
    "method": "function",
}

# Cache compiled queries keyed on SHA(tags.scm text). Most languages have
# stable queries; cache hit after first compile per process.
_QUERY_CACHE: dict[str, ts.Query] = {}


def _compile_query(language: ts.Language, query_text: str) -> ts.Query:
    key = hashlib.sha256(query_text.encode("utf-8")).hexdigest()
    cached = _QUERY_CACHE.get(key)
    if cached is not None:
        return cached
    query = ts.Query(language, query_text)
    _QUERY_CACHE[key] = query
    return query


def _node_text(code: bytes, node) -> str:
    return code[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _first_line(code: bytes, node) -> str:
    return _node_text(code, node).split("\n", 1)[0].strip()


def load_tags_query_text(package_name: str) -> str | None:
    """Return the upstream ``tags.scm`` text bundled with a tree-sitter
    grammar package, or ``None`` if the package is not installed or
    doesn't ship tags.scm.

    Callers should fall back to a vendored override path in that case.
    Today's audit: every grammar we depend on (python/js/ts/java/go/rust/c_sharp/
    elixir) ships an upstream tags.scm, so the fallback path is unused.
    """
    try:
        m = importlib.import_module(package_name)
    except ImportError:
        return None
    if m.__file__ is None:
        return None
    p = Path(m.__file__).parent / "queries" / "tags.scm"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def extract_defs_via_tags(
    language: ts.Language,
    tree,
    code: bytes,
    rel_path: str,
    query_text: str,
) -> list[SymbolRecord]:
    """Run a ``tags.scm`` query against a parsed tree and emit
    ``SymbolRecord`` objects.

    Walks the query's match results. Each match associates a
    ``@definition.<kind>`` capture (spanning the whole definition node)
    with one or more ``@name`` captures (the identifier(s) for the
    symbol). Pairs them up and records each as a ``SymbolRecord`` whose
    ``type`` field comes from :data:`_KIND_TO_TYPE` and whose
    ``parent_qualified_name`` is the name of the nearest enclosing
    ``@definition.*`` capture.

    Two-pass implementation:

    1. **Pass 1** walks every match and records ``(definition_node,
       name_node, kind)`` triples + a ``node_id → name`` map so that
       parent-qn lookup is O(depth) per record in pass 2.
    2. **Pass 2** emits the ``SymbolRecord``. For each definition, walks
       ancestors upward until the first node that's also a definition;
       that's the parent.

    Top-level definitions get ``parent_qualified_name=""`` (mirrors the
    walker convention).
    """
    query = _compile_query(language, query_text)
    cursor = ts.QueryCursor(query)

    # Pass 1: collect (def_node, name_node, kind) triples + name lookup table.
    # IMPORTANT: tree-sitter's Python bindings return a fresh wrapper object
    # for the same underlying node on each access — ``id(node)`` is NOT
    # stable across traversals (e.g., the wrapper returned by
    # ``cursor.matches()`` has a different id than the wrapper returned by
    # ``other_node.parent`` even when they point at the same underlying
    # node). Keying the lookup table on ``(start_byte, end_byte)`` instead
    # of ``id(node)`` sidesteps this — every distinct node in the tree has
    # a unique span.
    NodeKey = tuple[int, int]
    triples: list[tuple[ts.Node, ts.Node, str]] = []
    definition_span_to_name: dict[NodeKey, str] = {}

    for _pattern_idx, captures in cursor.matches(tree.root_node):
        # Find the @definition.<kind> capture in this match. The schema gives
        # exactly one definition-prefixed capture per match.
        kind_key = next((k for k in captures if k.startswith("definition.")), None)
        if kind_key is None:
            continue
        kind = kind_key.split(".", 1)[1]
        def_nodes = captures[kind_key]
        name_nodes = captures.get("name", [])
        if not def_nodes or not name_nodes:
            continue
        # Pair definitions with names. Most matches have 1:1; some patterns
        # capture multiple names per definition (e.g. multi-clause functions
        # in Elixir) — zip with the shorter list to skip name-only captures
        # from nested patterns.
        for def_node, name_node in zip(def_nodes, name_nodes, strict=False):
            name = _node_text(code, name_node)
            if not name:
                continue
            triples.append((def_node, name_node, kind))
            definition_span_to_name[(def_node.start_byte, def_node.end_byte)] = name

    # Pass 2: emit records with ancestor-walk parent_qn resolution.
    records: list[SymbolRecord] = []
    for def_node, _name_node, kind in triples:
        sym_type = _KIND_TO_TYPE.get(kind)
        if sym_type is None:
            continue
        name = definition_span_to_name[(def_node.start_byte, def_node.end_byte)]
        parent_qn = ""
        ancestor = def_node.parent
        while ancestor is not None:
            parent_name = definition_span_to_name.get((ancestor.start_byte, ancestor.end_byte))
            if parent_name is not None:
                parent_qn = parent_name
                break
            ancestor = ancestor.parent
        qualified_name = f"{parent_qn}.{name}" if parent_qn else name
        records.append(
            SymbolRecord(
                name=name,
                qualified_name=qualified_name,
                type=sym_type,
                file_path=rel_path,
                start_line=def_node.start_point[0] + 1,
                end_line=def_node.end_point[0] + 1,
                signature=_first_line(code, def_node),
                parent_qualified_name=parent_qn,
            )
        )
    return records
