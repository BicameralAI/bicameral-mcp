"""Tree-sitter pre-pass for diff line categorization.

Computes per-line ``(in_function_signature, in_docstring_slot)`` flags
that the per-language categorizers consume. Lives separately from
``diff_categorizer.py`` per the v2 audit's O3 split — the public API
stays thin (~150 LOC) and the tree-sitter integration owns its own
module (~120 LOC).

Failure-isolated: if tree-sitter is unavailable for the language at
runtime, the function returns an empty flag map and every line falls
back to its language module's text-only heuristics.
"""

from __future__ import annotations

from code_locator.indexing.symbol_extractor import _LANG_PACKAGE_MAP, _get_parser

# Per-language tree-sitter node-type tables.
#
# ``signature_nodes``: AST node types whose byte range covers the
# function/method signature lines. We map each line of the signature
# to ``in_function_signature=True``.
#
# ``function_body_block``: AST node type for the function body block.
# The first non-trivial statement inside this block, when it's a string
# literal node (Python), is the docstring slot.
#
# Languages without a first-class docstring concept (JS, TS, Go, Rust,
# Java, C#) leave ``docstring_string_node`` as ``None``; the per-language
# categorizer modules already handle their respective doc-comment forms
# via text patterns. The dispatcher's job in those languages is just
# the signature flag.
_LANGUAGE_AST: dict[str, dict] = {
    "python": {
        "signature_nodes": ("function_definition",),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": "string",
    },
    "javascript": {
        "signature_nodes": ("function_declaration", "method_definition", "arrow_function"),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": None,
    },
    "typescript": {
        "signature_nodes": ("function_declaration", "method_definition", "arrow_function"),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": None,
    },
    "go": {
        "signature_nodes": ("function_declaration", "method_declaration"),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": None,
    },
    "rust": {
        "signature_nodes": ("function_item",),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": None,
    },
    "java": {
        "signature_nodes": ("method_declaration", "constructor_declaration"),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": None,
    },
    "c_sharp": {
        "signature_nodes": ("method_declaration", "constructor_declaration"),
        "signature_field": "name",
        "body_field": "body",
        "docstring_node_type": None,
    },
}


def _line_of(byte_pos: int, line_starts: list[int]) -> int:
    """Binary-search the 1-indexed line number containing ``byte_pos``."""
    lo, hi = 0, len(line_starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if line_starts[mid] <= byte_pos:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1  # 1-indexed


def _build_line_starts(code: bytes) -> list[int]:
    """Byte offsets of each line's first byte (0-indexed in list)."""
    starts = [0]
    for i, b in enumerate(code):
        if b == 0x0A:  # '\n'
            starts.append(i + 1)
    return starts


def _flag_signature_lines(
    node,
    code: bytes,
    line_starts: list[int],
    sig_node_types: tuple,
    body_field: str,
    flags: dict[int, tuple[bool, bool]],
) -> None:
    """Walk the tree; for each function-like node, mark its signature
    lines (everything from node start to body start) with the
    in_function_signature flag.
    """
    if node.type in sig_node_types:
        first_line = _line_of(node.start_byte, line_starts)
        # Find the end-byte of the signature proper. Walk children
        # until the body field; track the end_byte of the last
        # NON-COMMENT child we saw. Comment nodes are tree-sitter
        # extras that can sit between the colon (Python) / opening
        # brace and the body block; treating them as part of the
        # signature would erase the cosmetic-comment signal.
        sig_end_byte = node.end_byte
        prev_end = node.start_byte
        for i, child in enumerate(node.children):
            field = node.field_name_for_child(i)
            if field == body_field:
                sig_end_byte = prev_end
                break
            if child.type == "comment":
                continue
            prev_end = child.end_byte
        last_line = _line_of(
            max(sig_end_byte - 1, node.start_byte),
            line_starts,
        )
        last_line = max(last_line, first_line)
        for ln in range(first_line, last_line + 1):
            cur_sig, cur_doc = flags.get(ln, (False, False))
            flags[ln] = (True, cur_doc)
    for child in node.children:
        _flag_signature_lines(
            child,
            code,
            line_starts,
            sig_node_types,
            body_field,
            flags,
        )


def _flag_docstring_lines(
    node,
    code: bytes,
    line_starts: list[int],
    sig_node_types: tuple,
    body_field: str,
    doc_type: str,
    flags: dict[int, tuple[bool, bool]],
) -> None:
    """For each function-like node, find the first statement of its
    body; if that statement wraps a string-literal node of the
    expected type, mark each of its lines with the in_docstring_slot
    flag."""
    if node.type in sig_node_types:
        body = node.child_by_field_name(body_field)
        if body is not None:
            first_stmt = next(
                (c for c in body.children if c.is_named),
                None,
            )
            if first_stmt is not None:
                # Python wraps the literal in expression_statement → string.
                doc_node = first_stmt
                if doc_node.type != doc_type:
                    doc_node = next(
                        (c for c in first_stmt.children if c.type == doc_type),
                        None,
                    )
                if doc_node is not None:
                    first_line = _line_of(doc_node.start_byte, line_starts)
                    last_line = _line_of(
                        max(doc_node.end_byte - 1, doc_node.start_byte),
                        line_starts,
                    )
                    for ln in range(first_line, last_line + 1):
                        cur_sig, _ = flags.get(ln, (False, False))
                        flags[ln] = (cur_sig, True)
    for child in node.children:
        _flag_docstring_lines(
            child,
            code,
            line_starts,
            sig_node_types,
            body_field,
            doc_type,
            flags,
        )


def compute_slot_flags(
    body: str,
    language: str,
) -> dict[int, tuple[bool, bool]]:
    """Return ``{line_number: (in_function_signature, in_docstring_slot)}``.

    Lines absent from the dict have both flags ``False``. Caller (the
    per-line categorizer) defaults missing lines to "no flags set".

    Returns ``{}`` on tree-sitter unavailable or unsupported language.
    """
    if language not in _LANGUAGE_AST or language not in _LANG_PACKAGE_MAP:
        return {}
    config = _LANGUAGE_AST[language]
    try:
        parser = _get_parser(language)
    except Exception:
        return {}
    code = body.encode("utf-8", errors="replace")
    try:
        tree = parser.parse(code)
    except Exception:
        return {}
    line_starts = _build_line_starts(code)
    flags: dict[int, tuple[bool, bool]] = {}
    _flag_signature_lines(
        tree.root_node,
        code,
        line_starts,
        config["signature_nodes"],
        config["body_field"],
        flags,
    )
    if config["docstring_node_type"] is not None:
        _flag_docstring_lines(
            tree.root_node,
            code,
            line_starts,
            config["signature_nodes"],
            config["body_field"],
            config["docstring_node_type"],
            flags,
        )
    return flags
