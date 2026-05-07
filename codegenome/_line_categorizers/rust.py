"""Rust line categorizer.

Rust comments split into:
  - ``//`` — plain line comment.
  - ``///`` — outer doc comment (precedes a definition; documentation).
  - ``//!`` — inner doc comment (inside a module/crate; documentation).
  - ``/* */`` — block comment.
  - ``/** */`` — outer doc block comment.

Doc comments are categorized as ``docstring``; plain comments as
``comment``. Same pattern as godoc — Rust's tooling consumes ``///``
and ``//!`` as documentation, so they should weight cosmetic.

``use`` lines are imports.
"""

from __future__ import annotations

from . import LineCategory


def _is_doc_comment(stripped: str) -> bool:
    return (
        stripped.startswith("///")
        or stripped.startswith("//!")
        or stripped.startswith("/**")
        or stripped.startswith("/*!")
    )


def _is_plain_comment(stripped: str) -> bool:
    return (
        stripped.startswith("//")
        or stripped.startswith("/*")
        or stripped.endswith("*/")
        or stripped.startswith("*")
    )


def _is_import(stripped: str) -> bool:
    return stripped.startswith("use ") or stripped.startswith("extern crate")


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one Rust source line."""
    if in_function_signature:
        return "signature"
    stripped = line.strip()
    if stripped == "":
        return "blank"
    # Doc-comment detection wins over plain-comment detection.
    if _is_doc_comment(stripped) or in_docstring_slot:
        return "docstring"
    if _is_plain_comment(stripped):
        return "comment"
    if _is_import(stripped):
        return "import"
    return "logic"
