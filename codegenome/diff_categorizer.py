"""Public API for the diff-line categorizer.

Given two source-code bodies (old / new) and a language ID, produces
a ``DiffStats`` count of changed lines bucketed by category. Callers
in the drift classifier use the cosmetic-leaning categories
(``comment``, ``docstring``, ``blank``) to compute the ``diff_lines``
signal weight (issue #61: 0.30 of the total score).

Implementation split per v2 audit's O3:

- Tree-sitter slot computation (signature / docstring lines) lives in
  ``_diff_dispatch.compute_slot_flags``.
- Per-language line classification rules live in
  ``_line_categorizers.<language>``.

This module is the thin public-facing dispatcher.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from . import _diff_dispatch
from ._line_categorizers import categorize as _categorize_line


@dataclass(frozen=True)
class DiffStats:
    """Bucketed counts of changed lines."""

    total: int
    comment: int
    docstring: int
    blank: int
    import_: int
    logic: int
    signature: int

    @property
    def cosmetic_ratio(self) -> float:
        """Fraction of changed lines that are cosmetic-class.

        Cosmetic = ``comment + docstring + blank``. ``import`` is NOT
        cosmetic — re-ordering imports can be cosmetic but adding a
        new import is not, and we can't tell those apart from line
        categories alone. Treat conservatively as logic-equivalent.
        """
        return (self.comment + self.docstring + self.blank) / self.total if self.total > 0 else 0.0


def _changed_lines(
    old_body: str,
    new_body: str,
) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Compute changed lines on each side via difflib.

    Returns ``(removed, added)`` where each list is
    ``[(line_number_in_source, content), ...]``. Line numbers are
    1-indexed and match positions in the respective body.
    """
    old_lines = old_body.splitlines()
    new_lines = new_body.splitlines()
    diff = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    removed: list[tuple[int, str]] = []
    added: list[tuple[int, str]] = []
    for tag, i1, i2, j1, j2 in diff.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            removed.append((i + 1, old_lines[i]))
        for j in range(j1, j2):
            added.append((j + 1, new_lines[j]))
    return removed, added


def _bucket(
    lines: list[tuple[int, str]],
    language: str,
    flags: dict,
) -> dict:
    """Count category occurrences for one side of the diff."""
    counts = {
        "comment": 0,
        "docstring": 0,
        "blank": 0,
        "import": 0,
        "logic": 0,
        "signature": 0,
    }
    for line_no, text in lines:
        sig_flag, doc_flag = flags.get(line_no, (False, False))
        cat = _categorize_line(
            language,
            text,
            in_function_signature=sig_flag,
            in_docstring_slot=doc_flag,
        )
        counts[cat] += 1
    return counts


def categorize_diff(
    old_body: str,
    new_body: str,
    language: str,
) -> DiffStats:
    """Categorize each changed line per-language. Public API.

    Caller must pre-validate ``language``; unsupported languages are a
    programming error here. The classifier entry-point
    (``codegenome.drift_classifier.classify_drift``) short-circuits
    unsupported languages to ``"uncertain"`` before this function is
    reached.
    """
    removed, added = _changed_lines(old_body, new_body)
    old_flags = _diff_dispatch.compute_slot_flags(old_body, language)
    new_flags = _diff_dispatch.compute_slot_flags(new_body, language)
    rem_counts = _bucket(removed, language, old_flags)
    add_counts = _bucket(added, language, new_flags)
    total = sum(rem_counts.values()) + sum(add_counts.values())
    return DiffStats(
        total=total,
        comment=rem_counts["comment"] + add_counts["comment"],
        docstring=rem_counts["docstring"] + add_counts["docstring"],
        blank=rem_counts["blank"] + add_counts["blank"],
        import_=rem_counts["import"] + add_counts["import"],
        logic=rem_counts["logic"] + add_counts["logic"],
        signature=rem_counts["signature"] + add_counts["signature"],
    )
