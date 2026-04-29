"""Java line categorizer.

Javadoc (``/** ... */``) preceding a method/class is treated as
``docstring`` ONLY when the dispatcher's pre-pass flags the line as
in the docstring slot; otherwise block comments are plain ``comment``
weight.
"""

from __future__ import annotations

from . import LineCategory


def _is_javadoc_open(stripped: str) -> bool:
    return stripped.startswith("/**")


def _is_block_comment(stripped: str) -> bool:
    return (
        stripped.startswith("/*")
        or stripped.startswith("*")
        or stripped.endswith("*/")
    )


def _is_line_comment(stripped: str) -> bool:
    return stripped.startswith("//")


def _is_import(stripped: str) -> bool:
    return stripped.startswith("import ") or stripped.startswith("package ")


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one Java source line."""
    if in_function_signature:
        return "signature"
    if in_docstring_slot:
        return "docstring"
    stripped = line.strip()
    if stripped == "":
        return "blank"
    if _is_javadoc_open(stripped):
        return "docstring"
    if _is_line_comment(stripped) or _is_block_comment(stripped):
        return "comment"
    if _is_import(stripped):
        return "import"
    return "logic"
