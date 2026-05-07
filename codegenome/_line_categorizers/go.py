"""Go line categorizer.

Go has no first-class docstrings — godoc convention is line comments
(``//``) immediately preceding a declaration. The dispatcher's pre-pass
detects that pattern and sets ``in_docstring_slot=True``; this module
treats both ``//``-line comments and ``/* */`` block comments as plain
comments otherwise.

``import`` covers both single-line (``import "fmt"``) and parenthesised
block forms; the dispatcher's pre-pass flags every line of an
``import (...)`` block as in-import via the AST.
"""

from __future__ import annotations

from . import LineCategory


def _is_comment(stripped: str) -> bool:
    if stripped.startswith("//"):
        return True
    if stripped.startswith("/*") or stripped.endswith("*/"):
        return True
    if stripped.startswith("*") and not stripped.startswith("**"):
        return True
    return False


def _is_import(stripped: str) -> bool:
    return (
        stripped.startswith("import ") or stripped.startswith("import(") or stripped == "import ("
    )


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one Go source line."""
    if in_function_signature:
        return "signature"
    if in_docstring_slot:
        return "docstring"
    stripped = line.strip()
    if stripped == "":
        return "blank"
    if _is_comment(stripped):
        return "comment"
    if _is_import(stripped):
        return "import"
    # Inside an `import (...)` block, lines are bare import paths.
    # The dispatcher sets the in-import flag through AST walk; we keep
    # a conservative fallback here for cases where the pre-pass missed.
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("_") and '"' in stripped
    ):
        return "import"
    return "logic"
