"""Build dependency edges (contains, imports, invokes) from indexed symbols.

Runs AFTER symbol extraction. Reads the symbol index from SQLite,
re-parses source files with tree-sitter, and inserts edges.
"""

from __future__ import annotations

import os
from pathlib import Path

from .sqlite_store import SymbolDB
from .symbol_extractor import (
    EXTENSION_LANGUAGE,
    _get_parser,
    _node_text,
)

# ── Contains edges ───────────────────────────────────────────────────


def _build_contains_edges(db: SymbolDB) -> list[tuple[int, int, str]]:
    """Build parent->child edges using parent_qualified_name."""
    conn = db._connect()

    # Get all symbols that have a parent
    children = conn.execute(
        "SELECT id, parent_qualified_name, file_path FROM symbols WHERE parent_qualified_name != ''"
    ).fetchall()

    edges: list[tuple[int, int, str]] = []
    for child in children:
        child_id = child[0]
        parent_qn = child[1]
        file_path = child[2]

        # Find the parent symbol in the same file
        parent = conn.execute(
            "SELECT id FROM symbols WHERE qualified_name = ? AND file_path = ?",
            (parent_qn, file_path),
        ).fetchone()

        if parent is not None:
            edges.append((parent[0], child_id, "contains"))

    return edges


# ── Import edges ─────────────────────────────────────────────────────


def _extract_python_imports(tree, code: bytes) -> list[str]:
    """Extract imported names from Python import statements."""
    names: list[str] = []

    def walk(node):
        if node.type == "import_statement":
            # import foo, bar
            for child in node.children:
                if child.type == "dotted_name":
                    names.append(_node_text(code, child).split(".")[-1])
                elif child.type == "aliased_import":
                    alias = child.child_by_field_name("alias")
                    if alias:
                        names.append(_node_text(code, alias))
                    else:
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            names.append(_node_text(code, name_node).split(".")[-1])
            return

        if node.type == "import_from_statement":
            # from foo import bar, baz
            for child in node.children:
                if (
                    child.type == "dotted_name"
                    and child.prev_sibling
                    and _node_text(code, child.prev_sibling) == "import"
                ):
                    names.append(_node_text(code, child))
                elif child.type == "aliased_import":
                    alias = child.child_by_field_name("alias")
                    if alias:
                        names.append(_node_text(code, alias))
                    else:
                        name_node = child.child_by_field_name("name")
                        if name_node:
                            names.append(_node_text(code, name_node))
                elif child.type == "import_prefix":
                    pass  # skip the "from" part
            return

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return names


def _extract_js_ts_imports(tree, code: bytes) -> list[str]:
    """Extract imported names from JS/TS import statements."""
    names: list[str] = []

    def walk(node):
        if node.type == "import_statement":
            # Look for import_clause -> named_imports -> import_specifier
            for child in node.children:
                if child.type == "import_clause":
                    for clause_child in child.children:
                        if clause_child.type == "identifier":
                            # default import
                            names.append(_node_text(code, clause_child))
                        elif clause_child.type == "named_imports":
                            for spec in clause_child.children:
                                if spec.type == "import_specifier":
                                    alias = spec.child_by_field_name("alias")
                                    if alias:
                                        names.append(_node_text(code, alias))
                                    else:
                                        name_node = spec.child_by_field_name("name")
                                        if name_node:
                                            names.append(_node_text(code, name_node))
                        elif clause_child.type == "namespace_import":
                            # import * as foo
                            for ns_child in clause_child.children:
                                if ns_child.type == "identifier":
                                    names.append(_node_text(code, ns_child))
            return

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return names


def _extract_go_imports(tree, code: bytes) -> list[str]:
    """Extract imported package names from Go import declarations."""
    names: list[str] = []

    def walk(node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "import_spec":
                    path_node = child.child_by_field_name("path")
                    if path_node:
                        path = _node_text(code, path_node).strip('"')
                        names.append(path.split("/")[-1])
                elif child.type == "import_spec_list":
                    for spec in child.children:
                        if spec.type == "import_spec":
                            # Check for alias
                            name_node = spec.child_by_field_name("name")
                            path_node = spec.child_by_field_name("path")
                            if name_node:
                                names.append(_node_text(code, name_node))
                            elif path_node:
                                path = _node_text(code, path_node).strip('"')
                                names.append(path.split("/")[-1])
            return

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return names


def _extract_java_imports(tree, code: bytes) -> list[str]:
    """Extract imported class names from Java import declarations."""
    names: list[str] = []

    def walk(node):
        if node.type == "import_declaration":
            for child in node.children:
                if child.type == "scoped_identifier":
                    name_node = child.child_by_field_name("name")
                    if name_node:
                        names.append(_node_text(code, name_node))
            return

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return names


def _extract_imports_for_language(language_id: str, tree, code: bytes) -> list[str]:
    """Dispatch to the right import extractor."""
    if language_id == "python":
        return _extract_python_imports(tree, code)
    if language_id in ("javascript", "jsx", "typescript", "tsx"):
        return _extract_js_ts_imports(tree, code)
    if language_id == "go":
        return _extract_go_imports(tree, code)
    if language_id == "java":
        return _extract_java_imports(tree, code)
    return []


# ── Invokes edges ────────────────────────────────────────────────────


def _extract_call_names(tree, code: bytes, language_id: str) -> list[tuple[int, str]]:
    """Extract (line_number, called_function_name) from call expressions.

    Returns 1-indexed line numbers.
    """
    calls: list[tuple[int, str]] = []
    call_types = {"call_expression", "call"}

    def walk(node):
        if node.type in call_types:
            # Get the function being called
            func_node = node.child_by_field_name("function")
            if func_node is None:
                # Some languages use "name" field
                func_node = node.child_by_field_name("name")
            if func_node is not None:
                text = _node_text(code, func_node)
                # Extract the last identifier (e.g., "foo.bar.baz" -> "baz")
                name = text.split(".")[-1].split("::")[-1]
                if name and name.isidentifier():
                    line = node.start_point[0] + 1  # 1-indexed
                    calls.append((line, name))

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return calls


# ── Main builder ─────────────────────────────────────────────────────


def build_graph(db: SymbolDB, repo_path: str) -> int:
    """Build dependency edges for all indexed symbols. Returns edge count."""
    # Clear old edges — full rebuild is fast relative to symbol extraction
    db.delete_all_edges()

    conn = db._connect()
    total_edges = 0

    # 1. Contains edges (purely from the symbol index, no re-parsing needed)
    contains_edges = _build_contains_edges(db)
    if contains_edges:
        db.insert_edges_batch(contains_edges)
        total_edges += len(contains_edges)

    # 2. Build a name->id lookup for import/invoke matching
    all_symbols = conn.execute(
        "SELECT id, name, qualified_name, file_path, type, start_line, end_line FROM symbols"
    ).fetchall()

    # Map: name -> list of symbol ids (multiple symbols can have the same name)
    name_to_ids: dict[str, list[int]] = {}
    for sym in all_symbols:
        name = sym[1]
        if name not in name_to_ids:
            name_to_ids[name] = []
        name_to_ids[name].append(sym[0])

    # Derive distinct files from all_symbols (avoids redundant query)
    indexed_file_paths = sorted({sym[3] for sym in all_symbols})

    for rel_path in indexed_file_paths:
        abs_path = os.path.join(repo_path, rel_path)

        ext = Path(rel_path).suffix.lower()
        language_id = EXTENSION_LANGUAGE.get(ext)
        if not language_id:
            continue

        try:
            parser = _get_parser(language_id)
        except Exception:
            continue

        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            continue
        code_bytes = source.encode("utf-8")
        tree = parser.parse(code_bytes)

        file_edges: list[tuple[int, int, str]] = []

        # 3. Import edges
        imported_names = _extract_imports_for_language(language_id, tree, code_bytes)

        # Get the file-level symbols (top-level functions/classes) to use as source
        file_symbols = conn.execute(
            "SELECT id FROM symbols WHERE file_path = ? AND parent_qualified_name = ''",
            (rel_path,),
        ).fetchall()
        file_symbol_ids: set[int] = {row[0] for row in file_symbols}

        # Also get ALL symbol ids in this file for dedup
        all_file_sym_ids: set[int] = set()
        file_all_symbols = conn.execute(
            "SELECT id FROM symbols WHERE file_path = ?", (rel_path,)
        ).fetchall()
        for row in file_all_symbols:
            all_file_sym_ids.add(row[0])

        seen_import_edges: set[tuple[int, int]] = set()
        for imp_name in imported_names:
            target_ids = name_to_ids.get(imp_name, [])
            for target_id in target_ids:
                # Don't create self-referencing edges within the same file
                if target_id in all_file_sym_ids:
                    continue
                # Each top-level symbol in this file imports the target
                for source_id in file_symbol_ids:
                    pair = (source_id, target_id)
                    if pair not in seen_import_edges:
                        seen_import_edges.add(pair)
                        file_edges.append((source_id, target_id, "imports"))

        # 4. Invokes edges
        call_sites = _extract_call_names(tree, code_bytes, language_id)

        # Get function symbols in this file with their line ranges
        func_symbols = conn.execute(
            "SELECT id, name, start_line, end_line FROM symbols WHERE file_path = ? AND type = 'function'",
            (rel_path,),
        ).fetchall()

        seen_invoke_edges: set[tuple[int, int]] = set()
        for func in func_symbols:
            func_id = func[0]
            func_start = func[2]
            func_end = func[3]

            # Find calls within this function's line range
            for call_line, call_name in call_sites:
                if call_line < func_start or call_line > func_end:
                    continue
                # Skip self-calls by name
                if call_name == func[1]:
                    continue
                target_ids = name_to_ids.get(call_name, [])
                for target_id in target_ids:
                    if target_id == func_id:
                        continue
                    pair = (func_id, target_id)
                    if pair not in seen_invoke_edges:
                        seen_invoke_edges.add(pair)
                        file_edges.append((func_id, target_id, "invokes"))

        if file_edges:
            db.insert_edges_batch(file_edges)
            total_edges += len(file_edges)

    return total_edges
