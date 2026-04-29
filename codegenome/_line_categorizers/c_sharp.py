"""C# line categorizer.

C# XML documentation comments (``///``) and ``/** */`` are treated as
``docstring``; plain ``//`` comments and ``/* */`` blocks are
``comment``. ``using`` directives are imports; ``namespace`` is a
structural directive that we treat as ``logic`` (it changes
declarations, not just style).

PR #73 v2 audit F3 + F4: this module's filename is ``c_sharp.py`` to
match ``code_locator.indexing.symbol_extractor._LANG_PACKAGE_MAP``'s
``"c_sharp"`` key exactly.
"""

from __future__ import annotations

from . import LineCategory


def _is_xml_doc(stripped: str) -> bool:
    return stripped.startswith("///")


def _is_block_comment(stripped: str) -> bool:
    return stripped.startswith("/*") or stripped.startswith("*") or stripped.endswith("*/")


def _is_line_comment(stripped: str) -> bool:
    # Must check XML doc FIRST (also starts with `/`).
    return stripped.startswith("//") and not stripped.startswith("///")


def _is_import(stripped: str) -> bool:
    # `using` directive (top-level). The `using (resource)` C# 8 form
    # is a statement, not an import — we don't disambiguate here
    # because the cosmetic weighting treats both as low-impact.
    return stripped.startswith("using ")


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one C# source line."""
    if in_function_signature:
        return "signature"
    if in_docstring_slot:
        return "docstring"
    stripped = line.strip()
    if stripped == "":
        return "blank"
    if _is_xml_doc(stripped):
        return "docstring"
    if _is_line_comment(stripped) or _is_block_comment(stripped):
        return "comment"
    if _is_import(stripped):
        return "import"
    return "logic"
