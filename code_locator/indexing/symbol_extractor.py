"""Tree-sitter symbol extraction for 10 languages.

Ported from tools/bicameral-locagent/dependency_graph/build_graph.py
and adapted to produce SymbolRecord objects for the SQLite store.

Per-language coverage:

- Python, JS/JSX, TS/TSX, Java, Go, Rust, C#: bespoke walkers
  (``_extract_<lang>_defs``) — historical pattern.
- **Elixir (#367)**: generic tags-query path via
  ``tags_extractor.extract_defs_via_tags`` against the upstream
  ``tree-sitter-elixir`` grammar's ``queries/tags.scm``. Substrate
  introduced in #367 as the precursor to the broader hybrid refactor.

Dispatch routing (#399 Stage C):

  Per-language ``ShadowMode`` enum in ``_SHADOW_MODES`` controls how
  ``_extract_definitions`` routes a given language. Four modes per the
  #399 rollout pattern:

  - ``walker-only`` — runs the bespoke walker, authoritative. Current
    state for Python/Go/Rust/JS/TS/Java/C#.
  - ``shadow-substrate`` — runs walker AND substrate; walker
    authoritative, divergence logged to ``m_shadow_divergence``
    telemetry. Stage D flips Python/Go/Rust here.
  - ``shadow-walker`` — runs walker AND substrate; substrate
    authoritative, divergence logged. Stage E pre-retirement.
  - ``substrate-only`` — runs the tags-query substrate only. Current
    state for Elixir (no walker exists).

  Per-language ``_WALKER_VOCAB`` defines the symbol-type vocabulary
  the walker emits for that language. Shadow mode filters substrate
  output to this vocabulary before comparing — substrate-only kinds
  (e.g. Rust ``macro``, Python ``constant``) are by design and aren't
  divergences.

  Walker retirement is staged behind these modes; mode flips are one-
  line edits to ``_SHADOW_MODES`` that ship in their own small PRs
  (see #399 Stages D and E).
"""

from __future__ import annotations

from enum import StrEnum

from .sqlite_store import SymbolRecord

# ── Language mappings ────────────────────────────────────────────────

EXTENSION_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "jsx",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cs": "c_sharp",
    ".ex": "elixir",
    ".exs": "elixir",
}

LANGUAGE_FALLBACK = {
    "jsx": "javascript",
    "tsx": "typescript",
}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

# ── Tree-sitter backend detection ────────────────────────────────────
# Supports two backends:
#   1. tree_sitter_languages (legacy, Python <=3.12)
#   2. Individual tree-sitter-{lang} packages + tree-sitter>=0.22 (Python 3.13+)

_USE_LEGACY = False

try:
    from tree_sitter_languages import get_language as _legacy_get_language
    from tree_sitter_languages import get_parser as _legacy_get_parser

    _USE_LEGACY = True
except Exception:
    _legacy_get_language = None
    _legacy_get_parser = None

# Individual language packages for the modern API
_LANG_MODULES: dict[str, object] = {}

if not _USE_LEGACY:
    try:
        import tree_sitter as _ts
    except ImportError:
        _ts = None

    _LANG_PACKAGE_MAP = {
        "python": "tree_sitter_python",
        "javascript": "tree_sitter_javascript",
        "typescript": "tree_sitter_typescript",
        "java": "tree_sitter_java",
        "go": "tree_sitter_go",
        "rust": "tree_sitter_rust",
        "c_sharp": "tree_sitter_c_sharp",
        "elixir": "tree_sitter_elixir",
    }

# ── Parser caching ───────────────────────────────────────────────────

PARSER_CACHE: dict[str, object] = {}
LANGUAGE_CACHE: dict[str, object] = {}


def _get_language_obj(resolved: str):
    """Get a tree-sitter Language object for the resolved language name."""
    if _USE_LEGACY:
        return _legacy_get_language(resolved)

    if _ts is None:
        raise ImportError("tree-sitter is required.")

    pkg_name = _LANG_PACKAGE_MAP.get(resolved)
    if pkg_name is None:
        raise ImportError(f"No tree-sitter package mapping for language: {resolved}")

    if pkg_name not in _LANG_MODULES:
        import importlib

        mod = importlib.import_module(pkg_name)
        _LANG_MODULES[pkg_name] = mod

    mod = _LANG_MODULES[pkg_name]
    # typescript package exposes language_typescript() and language_tsx()
    if resolved == "typescript" and hasattr(mod, "language_typescript"):
        return _ts.Language(mod.language_typescript())
    return _ts.Language(mod.language())


def _get_parser(language_id: str):
    resolved = LANGUAGE_FALLBACK.get(language_id, language_id)
    if resolved not in PARSER_CACHE:
        if _USE_LEGACY:
            PARSER_CACHE[resolved] = _legacy_get_parser(resolved)
        else:
            if _ts is None:
                raise ImportError("tree-sitter is required.")
            lang = _get_language_obj(resolved)
            PARSER_CACHE[resolved] = _ts.Parser(lang)
    return PARSER_CACHE[resolved]


# ── Helpers ──────────────────────────────────────────────────────────


def _node_text(code: bytes, node) -> str:
    return code[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _get_name_from_node(node, code: bytes) -> str | None:
    name_node = node.child_by_field_name("name")
    if name_node is None:
        return None
    return _node_text(code, name_node)


def _first_line(code: bytes, node) -> str:
    text = _node_text(code, node)
    return text.split("\n", 1)[0].strip()


def _make_record(
    rel_path: str,
    node,
    code: bytes,
    sym_type: str,
    name: str,
    qualified_name: str,
    parent_qualified_name: str,
) -> SymbolRecord:
    return SymbolRecord(
        name=name,
        qualified_name=qualified_name,
        type=sym_type,
        file_path=rel_path,
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=_first_line(code, node),
        parent_qualified_name=parent_qualified_name,
    )


# ── Python ───────────────────────────────────────────────────────────


def _extract_python_defs(tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []

    def walk(node, class_stack: list[str]):
        if node.type == "class_definition":
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = ".".join(class_stack + [name]) if class_stack else name
            parent_qn = ".".join(class_stack) if class_stack else ""
            records.append(_make_record(rel_path, node, code, "class", name, qn, parent_qn))
            class_stack.append(name)
            for child in node.children:
                walk(child, class_stack)
            class_stack.pop()
            return

        if node.type in ("function_definition", "async_function_definition"):
            name = _get_name_from_node(node, code)
            if not name:
                return
            if class_stack:
                qn = f"{'.'.join(class_stack)}.{name}"
                parent_qn = ".".join(class_stack)
            else:
                qn = name
                parent_qn = ""
            records.append(_make_record(rel_path, node, code, "function", name, qn, parent_qn))
            return

        for child in node.children:
            walk(child, class_stack)

    walk(tree.root_node, [])
    return records


# ── JavaScript / TypeScript / JSX / TSX ──────────────────────────────


def _extract_js_ts_defs(tree, code: bytes, rel_path: str, language_id: str) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []

    class_types = {"class_declaration"}
    if language_id in ("typescript", "tsx"):
        class_types.update({"interface_declaration", "type_alias_declaration", "enum_declaration"})

    def walk(node, class_stack: list[str]):
        if node.type in class_types:
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = ".".join(class_stack + [name]) if class_stack else name
            parent_qn = ".".join(class_stack) if class_stack else ""
            records.append(_make_record(rel_path, node, code, "class", name, qn, parent_qn))
            class_stack.append(name)
            for child in node.children:
                walk(child, class_stack)
            class_stack.pop()
            return

        if node.type == "method_definition":
            if not class_stack:
                return
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = f"{'.'.join(class_stack)}.{name}"
            parent_qn = ".".join(class_stack)
            records.append(_make_record(rel_path, node, code, "function", name, qn, parent_qn))
            return

        if node.type == "function_declaration":
            if class_stack:
                return
            name = _get_name_from_node(node, code)
            if not name:
                return
            records.append(_make_record(rel_path, node, code, "function", name, name, ""))
            return

        if node.type == "variable_declarator":
            if class_stack:
                return
            value_node = node.child_by_field_name("value")
            if value_node is None or value_node.type not in ("arrow_function", "function"):
                return
            name_node = node.child_by_field_name("name")
            if name_node is None:
                return
            name = _node_text(code, name_node)
            records.append(_make_record(rel_path, node, code, "function", name, name, ""))
            return

        for child in node.children:
            walk(child, class_stack)

    walk(tree.root_node, [])
    return records


# ── Java ─────────────────────────────────────────────────────────────


def _extract_java_defs(tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    class_types = {"class_declaration", "interface_declaration", "enum_declaration"}

    def walk(node, class_stack: list[str]):
        if node.type in class_types:
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = ".".join(class_stack + [name]) if class_stack else name
            parent_qn = ".".join(class_stack) if class_stack else ""
            records.append(_make_record(rel_path, node, code, "class", name, qn, parent_qn))
            class_stack.append(name)
            for child in node.children:
                walk(child, class_stack)
            class_stack.pop()
            return

        if node.type in ("method_declaration", "constructor_declaration"):
            if not class_stack:
                return
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = f"{'.'.join(class_stack)}.{name}"
            parent_qn = ".".join(class_stack)
            records.append(_make_record(rel_path, node, code, "function", name, qn, parent_qn))
            return

        for child in node.children:
            walk(child, class_stack)

    walk(tree.root_node, [])
    return records


# ── Go ───────────────────────────────────────────────────────────────


def _extract_go_defs(tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []

    def walk(node, class_stack: list[str]):
        if node.type == "type_spec":
            type_node = node.child_by_field_name("type")
            if type_node is not None and type_node.type in ("struct_type", "interface_type"):
                name_node = node.child_by_field_name("name")
                if name_node is None:
                    return
                name = _node_text(code, name_node)
                qn = ".".join(class_stack + [name]) if class_stack else name
                parent_qn = ".".join(class_stack) if class_stack else ""
                records.append(_make_record(rel_path, node, code, "class", name, qn, parent_qn))
                return

        if node.type in ("function_declaration", "method_declaration"):
            name = _get_name_from_node(node, code)
            if not name:
                return
            if class_stack:
                qn = f"{'.'.join(class_stack)}.{name}"
                parent_qn = ".".join(class_stack)
            else:
                qn = name
                parent_qn = ""
            records.append(_make_record(rel_path, node, code, "function", name, qn, parent_qn))
            return

        for child in node.children:
            walk(child, class_stack)

    walk(tree.root_node, [])
    return records


# ── Rust ─────────────────────────────────────────────────────────────


def _extract_rust_defs(tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    class_types = {"struct_item", "enum_item", "trait_item"}

    def walk(node, class_stack: list[str]):
        if node.type in class_types:
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = ".".join(class_stack + [name]) if class_stack else name
            parent_qn = ".".join(class_stack) if class_stack else ""
            records.append(_make_record(rel_path, node, code, "class", name, qn, parent_qn))
            return

        if node.type == "function_item":
            name = _get_name_from_node(node, code)
            if not name:
                return
            records.append(_make_record(rel_path, node, code, "function", name, name, ""))
            return

        for child in node.children:
            walk(child, class_stack)

    walk(tree.root_node, [])
    return records


# ── C# ───────────────────────────────────────────────────────────────


def _extract_csharp_defs(tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    records: list[SymbolRecord] = []
    class_types = {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "enum_declaration",
    }

    def walk(node, class_stack: list[str]):
        if node.type in class_types:
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = ".".join(class_stack + [name]) if class_stack else name
            parent_qn = ".".join(class_stack) if class_stack else ""
            records.append(_make_record(rel_path, node, code, "class", name, qn, parent_qn))
            class_stack.append(name)
            for child in node.children:
                walk(child, class_stack)
            class_stack.pop()
            return

        if node.type in ("method_declaration", "constructor_declaration"):
            if not class_stack:
                return
            name = _get_name_from_node(node, code)
            if not name:
                return
            qn = f"{'.'.join(class_stack)}.{name}"
            parent_qn = ".".join(class_stack)
            records.append(_make_record(rel_path, node, code, "function", name, qn, parent_qn))
            return

        for child in node.children:
            walk(child, class_stack)

    walk(tree.root_node, [])
    return records


# ── Shadow-mode dispatch (#399 Stage C) ──────────────────────────────


class ShadowMode(StrEnum):
    """How ``_extract_definitions`` routes a given language.

    See module docstring for the four-stage rollout pattern. Each
    transition (walker-only → shadow-substrate → shadow-walker →
    substrate-only) is a one-line edit to ``_SHADOW_MODES``, gated by
    the observation windows in #399 Stages D and E.
    """

    WALKER_ONLY = "walker-only"
    SHADOW_SUBSTRATE = "shadow-substrate"  # walker authoritative, substrate runs silently
    SHADOW_WALKER = "shadow-walker"  # substrate authoritative, walker runs silently
    SUBSTRATE_ONLY = "substrate-only"


# Per-language dispatch table. Initial state per #399's locked rollout:
# every walker-backed language starts at WALKER_ONLY; Elixir is
# substrate-only because no walker was ever written.
#
# JS/TS, Java, C# are documented as walker-only forever (#399 Stage A):
# the upstream tags.scm grammars have gaps these walkers fill (JS/TS
# overlap, Java annotation/wildcard handling, C# nested-class qualified
# names). Stage D/E flips do NOT apply to these three languages.
_SHADOW_MODES: dict[str, ShadowMode] = {
    "python": ShadowMode.WALKER_ONLY,
    "javascript": ShadowMode.WALKER_ONLY,
    "jsx": ShadowMode.WALKER_ONLY,
    "typescript": ShadowMode.WALKER_ONLY,
    "tsx": ShadowMode.WALKER_ONLY,
    "java": ShadowMode.WALKER_ONLY,
    "go": ShadowMode.WALKER_ONLY,
    "rust": ShadowMode.WALKER_ONLY,
    "c_sharp": ShadowMode.WALKER_ONLY,
    "elixir": ShadowMode.SUBSTRATE_ONLY,
}


# Per-language walker vocabulary — the ``SymbolRecord.type`` values
# the bespoke walker emits for this language. Used by shadow-mode
# dispatch to filter substrate output to a comparable vocabulary
# before computing divergence. Substrate-only kinds outside this set
# (e.g. Python ``constant``, Rust ``macro``, JS ``module``) are by
# design and aren't divergences.
#
# Locked in by ``tests/test_tags_extractor_parity.py`` — these match
# ``PY_WALKER_VOCAB`` / ``GO_WALKER_VOCAB`` / ``RUST_WALKER_VOCAB``
# defined there.
_WALKER_VOCAB: dict[str, frozenset[str]] = {
    "python": frozenset({"function", "class", "method"}),
    "javascript": frozenset({"function", "class"}),
    "jsx": frozenset({"function", "class"}),
    "typescript": frozenset({"function", "class"}),
    "tsx": frozenset({"function", "class"}),
    "java": frozenset({"function", "class"}),
    "go": frozenset({"function", "class"}),
    "rust": frozenset({"function", "class"}),
    "c_sharp": frozenset({"function", "class"}),
    # Elixir is substrate-only — no walker, no vocab.
}


def _run_walker(language_id: str, tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    """Dispatch to the bespoke walker for ``language_id``.

    Returns ``[]`` if the language has no walker — caller should check
    ``_SHADOW_MODES`` before calling and never invoke this for Elixir.
    """
    if language_id == "python":
        return _extract_python_defs(tree, code, rel_path)
    if language_id in ("javascript", "jsx", "typescript", "tsx"):
        return _extract_js_ts_defs(tree, code, rel_path, language_id)
    if language_id == "java":
        return _extract_java_defs(tree, code, rel_path)
    if language_id == "go":
        return _extract_go_defs(tree, code, rel_path)
    if language_id == "rust":
        return _extract_rust_defs(tree, code, rel_path)
    if language_id == "c_sharp":
        return _extract_csharp_defs(tree, code, rel_path)
    return []


def _run_substrate(language_id: str, tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    """Dispatch to the tags-query substrate for ``language_id``.

    Returns ``[]`` if the grammar package's ``queries/tags.scm`` is
    missing — matches the pre-Stage-C Elixir fallback behavior.
    """
    from .tags_extractor import extract_defs_via_tags, load_tags_query_text

    pkg_name = _LANG_PACKAGE_MAP.get(language_id)
    if pkg_name is None:
        return []
    query_text = load_tags_query_text(pkg_name)
    if query_text is None:
        return []
    try:
        lang = _get_language_obj(language_id)
    except (ImportError, KeyError):
        return []
    return extract_defs_via_tags(lang, tree, code, rel_path, query_text)


def _log_shadow_divergence(
    language_id: str,
    mode: ShadowMode,
    rel_path: str,
    walker_records: list[SymbolRecord],
    substrate_records: list[SymbolRecord],
) -> None:
    """Compute the (name, type) sets, filter to walker vocabulary, and
    hand them to the divergence log. Wrapped in try/except so a
    telemetry failure can never break extraction — same pattern as
    ``handlers/bind.py``'s m2 telemetry call site.
    """
    vocab = _WALKER_VOCAB.get(language_id)
    if vocab is None:
        return  # no walker vocab → no meaningful comparison
    walker_set = {(r.name, r.type) for r in walker_records if r.type in vocab}
    substrate_set = {(r.name, r.type) for r in substrate_records if r.type in vocab}
    try:
        from m_shadow_divergence_log import record_divergence

        record_divergence(
            language_id=language_id,
            mode=mode.value,
            rel_path=rel_path,
            walker_set=walker_set,
            substrate_set=substrate_set,
        )
    except Exception:
        # Telemetry must never break extraction. Swallow silently —
        # the import-side fail-loud invariant is for the user-facing
        # parity gate, not the in-process indexer.
        pass


def _extract_definitions(language_id: str, tree, code: bytes, rel_path: str) -> list[SymbolRecord]:
    """Extract definitions for ``language_id``, routing through the
    shadow-mode dispatch table.

    Unknown languages return ``[]`` — preserves the pre-Stage-C
    behavior for an extension whose grammar isn't wired.
    """
    mode = _SHADOW_MODES.get(language_id)
    if mode is None:
        return []

    if mode is ShadowMode.WALKER_ONLY:
        return _run_walker(language_id, tree, code, rel_path)

    if mode is ShadowMode.SUBSTRATE_ONLY:
        return _run_substrate(language_id, tree, code, rel_path)

    # Shadow modes: run BOTH, log divergence, return authoritative side.
    walker_records = _run_walker(language_id, tree, code, rel_path)
    substrate_records = _run_substrate(language_id, tree, code, rel_path)

    _log_shadow_divergence(language_id, mode, rel_path, walker_records, substrate_records)

    if mode is ShadowMode.SHADOW_SUBSTRATE:
        return walker_records  # walker authoritative
    if mode is ShadowMode.SHADOW_WALKER:
        return substrate_records  # substrate authoritative

    # Unreachable — exhaustive over the four enum members. Defensive
    # fallback returns the walker side so the indexer never crashes
    # on a future enum value introduced without updating this branch.
    return walker_records


# ── Public API ───────────────────────────────────────────────────────


def extract_symbols_from_content(
    content: str, language_id: str, rel_path: str
) -> list[SymbolRecord]:
    """Extract symbols from source code content (no file I/O).

    Args:
        content: Source code as a string.
        language_id: Language identifier (e.g. "python", "javascript").
        rel_path: Relative file path from repo root.

    Returns:
        List of SymbolRecord objects found in the content.
    """
    code_bytes = content.encode("utf-8")
    try:
        parser = _get_parser(language_id)
    except Exception:
        return []
    tree = parser.parse(code_bytes)
    return _extract_definitions(language_id, tree, code_bytes, rel_path)


def extract_symbols(file_path: str, repo_root: str) -> list[SymbolRecord]:
    """Extract symbols from a single file.

    Args:
        file_path: Absolute path to the source file.
        repo_root: Absolute path to the repository root.

    Returns:
        List of SymbolRecord objects found in the file.
    """
    from pathlib import Path

    ext = Path(file_path).suffix.lower()
    language_id = EXTENSION_LANGUAGE.get(ext)
    if not language_id:
        return []

    rel_path = Path(file_path).relative_to(repo_root).as_posix()

    with open(file_path, encoding="utf-8", errors="replace") as f:
        source = f.read()

    return extract_symbols_from_content(source, language_id, rel_path)
