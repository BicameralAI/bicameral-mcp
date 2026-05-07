"""JavaScript line categorizer.

JS has no docstrings as a language concept; JSDoc block comments
(``/** ... */``) above a function are treated as ``docstring`` ONLY
when the dispatcher's tree-sitter pre-pass marks them as occupying
the docstring slot (i.e. immediately preceding a function declaration).
Otherwise they're plain ``comment`` lines — they still count toward
the cosmetic signal but with the comment weight.
"""

from __future__ import annotations

from . import LineCategory


def _is_block_comment(stripped: str) -> bool:
    return stripped.startswith("/*") or stripped.startswith("*") or stripped.endswith("*/")


def _is_line_comment(stripped: str) -> bool:
    return stripped.startswith("//")


def _is_import(stripped: str) -> bool:
    # ES module + CJS require patterns. Excludes dynamic ``import()``.
    if stripped.startswith(("import ", "import{", "import*")):
        return True
    if stripped.startswith("export ") and "from " in stripped:
        return True
    if "require(" in stripped and "=" in stripped:
        return True
    return False


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one JavaScript source line."""
    if in_function_signature:
        return "signature"
    if in_docstring_slot:
        return "docstring"
    stripped = line.strip()
    if stripped == "":
        return "blank"
    if _is_line_comment(stripped) or _is_block_comment(stripped):
        return "comment"
    if _is_import(stripped):
        return "import"
    return "logic"
