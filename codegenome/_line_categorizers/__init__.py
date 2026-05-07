"""Per-language line categorizer registry.

Each module under this package exposes a single public function:

    def categorize_line(
        line: str, *, in_function_signature: bool, in_docstring_slot: bool,
    ) -> LineCategory

where ``LineCategory`` is one of
``"comment" | "docstring" | "blank" | "import" | "logic" | "signature"``.

The dispatcher (``codegenome.diff_categorizer.categorize_diff``) computes
the two flag arguments via tree-sitter (in ``codegenome._diff_dispatch``)
and calls the matching language module's ``categorize_line`` for each
changed line.

This split exists per O3 from the v2 audit: keeping the per-language
modules tiny (~30-80 LOC each) makes them razor-compliant and lets each
language's edge cases live next to each other rather than tangled in one
mega-file.
"""

from __future__ import annotations

from typing import Literal

LineCategory = Literal[
    "comment",
    "docstring",
    "blank",
    "import",
    "logic",
    "signature",
]


def categorize(
    language: str,
    line: str,
    *,
    in_function_signature: bool = False,
    in_docstring_slot: bool = False,
) -> LineCategory:
    """Dispatch ``line`` to the language's ``categorize_line``.

    Unknown languages default to ``"logic"`` — the conservative fallback
    that does NOT count toward the cosmetic-leaning ``diff_lines``
    signal weight.
    """
    from . import c_sharp, go, java, javascript, python, rust, typescript

    table = {
        "python": python.categorize_line,
        "javascript": javascript.categorize_line,
        "typescript": typescript.categorize_line,
        "go": go.categorize_line,
        "rust": rust.categorize_line,
        "java": java.categorize_line,
        "c_sharp": c_sharp.categorize_line,
    }
    fn = table.get(language)
    if fn is None:
        return "logic"
    return fn(
        line,
        in_function_signature=in_function_signature,
        in_docstring_slot=in_docstring_slot,
    )
