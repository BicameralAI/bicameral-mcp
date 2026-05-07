"""TypeScript line categorizer.

Extends the JavaScript rules with one TS-specific case: a line that
contains ONLY a type annotation (e.g. ``x: number;`` standalone, or
``  : Promise<void>``) is treated as ``comment``-equivalent for the
cosmetic signal — adding a type annotation alone does not change
runtime behaviour.

In practice the heuristic kicks in only when the dispatcher's
pre-pass identifies a "type-annotation-only" line; the conservative
fallback delegates to the JavaScript rules.
"""

from __future__ import annotations

from . import LineCategory
from .javascript import categorize_line as _js_categorize


def categorize_line(
    line: str,
    *,
    in_function_signature: bool,
    in_docstring_slot: bool,
) -> LineCategory:
    """Classify one TypeScript source line.

    Falls through to the JavaScript categorizer for everything except
    explicit type-annotation-only lines (handled by the dispatcher's
    flag computation, not here — this function is a thin wrapper that
    keeps the language-dispatch table simple).
    """
    return _js_categorize(
        line,
        in_function_signature=in_function_signature,
        in_docstring_slot=in_docstring_slot,
    )
