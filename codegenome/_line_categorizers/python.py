"""Python line categorizer for the diff-cosmetic signal.

Categorizes a single source line as one of: ``comment``, ``docstring``,
``blank``, ``import``, ``logic``, ``signature``.

The two flag arguments come from a tree-sitter pre-pass in
``codegenome._diff_dispatch.compute_slot_flags``:

- ``in_function_signature``: line is part of a ``def`` / ``async def``
  signature spanning one or more lines.
- ``in_docstring_slot``: line is inside the canonical first-statement
  string-literal docstring slot of a function/class/module.
"""

from __future__ import annotations

from . import LineCategory


def _is_comment(stripped: str) -> bool:
    return stripped.startswith("#")


def _is_blank(stripped: str) -> bool:
    return stripped == ""


def _is_import(stripped: str) -> bool:
    return stripped.startswith(("import ", "from "))


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one Python source line.

    Order of precedence:

    1. Function signature line wins (so ``def foo(x):`` is signature,
       not logic, even though it contains an identifier).
    2. Docstring slot wins for any line that is part of the docstring
       triple-quoted block (the dispatcher pre-computes this).
    3. Pure whitespace → blank.
    4. Comment-only line (after lstrip) → comment.
    5. ``import`` / ``from ... import`` → import.
    6. Everything else → logic.
    """
    if in_function_signature:
        return "signature"
    if in_docstring_slot:
        return "docstring"
    stripped = line.strip()
    if _is_blank(stripped):
        return "blank"
    if _is_comment(stripped):
        return "comment"
    if _is_import(stripped):
        return "import"
    return "logic"
