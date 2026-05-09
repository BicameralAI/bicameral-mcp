"""Unit tests for the M2 failure-mode classifier (#280 PR #292).

Pure-function tests on ``classify_failure_mode`` — no API, no ledger, no
surrealdb dependency. Table-driven across all 10 categories so a future
change to the classifier shows up as a single failing row, not a black
box.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests"))

from eval_grounding_recall import FAILURE_MODE_NEXT_STEPS, classify_failure_mode  # type: ignore[import-not-found]  # noqa: E402, I001


# Each case: (label, row_dict, expected_failure_mode)
_CASES: list[tuple[str, dict, str]] = [
    (
        "correct → correct",
        {"outcome": "correct", "case_type": "same_name_different_module"},
        "correct",
    ),
    (
        "wrong_file on case-A → wrong_module",
        {"outcome": "wrong_file", "case_type": "same_name_different_module"},
        "wrong_module",
    ),
    (
        "wrong_file on case-B → wrong_intent",
        {"outcome": "wrong_file", "case_type": "similar_intent"},
        "wrong_intent",
    ),
    (
        "wrong_file on case-C → cross_language_confusion",
        {"outcome": "wrong_file", "case_type": "cross_language"},
        "cross_language_confusion",
    ),
    (
        "wrong_symbol → wrong_symbol_in_right_file",
        {"outcome": "wrong_symbol", "case_type": "same_name_different_module"},
        "wrong_symbol_in_right_file",
    ),
    (
        "handler reject (#280 not found) → hallucinated_symbol",
        {
            "outcome": "wrong_file",
            "case_type": "similar_intent",
            "error_msg": "symbol 'fake' not found in foo.py at HEAD — caller-supplied "
            "line range cannot bypass symbol verification (#280)",
        },
        "hallucinated_symbol",
    ),
    (
        "handler reject (#280 span mismatch) → span_mismatch",
        {
            "outcome": "wrong_file",
            "case_type": "similar_intent",
            "error_msg": "symbol 'foo' resolves at lines 10-30 but caller supplied "
            "1-5 — span mismatch (#280)",
        },
        "span_mismatch",
    ),
    (
        "aborted on default-bind row → aborted_incorrectly",
        {"outcome": "aborted", "case_type": "similar_intent"},
        "aborted_incorrectly",
    ),
    (
        "aborted on §B ungroundable row → aborted_correctly",
        {
            "outcome": "aborted",
            "case_type": "behavioral",
            "expected_outcome": "abort",
        },
        "aborted_correctly",
    ),
    (
        "eval_error → eval_error",
        {"outcome": "eval_error", "case_type": "cross_language", "error_msg": "ReadTimeout"},
        "eval_error",
    ),
]


@pytest.mark.parametrize("label, row, expected", _CASES, ids=[c[0] for c in _CASES])
def test_classify_failure_mode_table(label: str, row: dict, expected: str) -> None:
    got = classify_failure_mode(row)
    assert got == expected, f"{label}: got {got!r}, expected {expected!r}"


def test_classify_failure_mode_handles_unknown_outcome() -> None:
    """Defensive: an outcome we haven't enumerated falls into 'uncategorized'
    rather than crashing the renderer."""
    row = {"outcome": "synthetic_future_outcome", "case_type": "similar_intent"}
    assert classify_failure_mode(row) == "uncategorized"


def test_failure_mode_taxonomy_documented() -> None:
    """Every category produced by classify_failure_mode must have a
    documented next-step in FAILURE_MODE_NEXT_STEPS. Catches taxonomy drift
    where someone adds a new category to the classifier but forgets the
    PM-facing hint."""
    produced = {expected for _, _, expected in _CASES}
    produced.add("uncategorized")  # documented but not in the table
    documented = set(FAILURE_MODE_NEXT_STEPS.keys())
    assert produced.issubset(documented), (
        f"missing from FAILURE_MODE_NEXT_STEPS: {produced - documented}"
    )


def test_classify_priority_handler_reject_over_case_type() -> None:
    """When a row carries BOTH a #280 error_msg AND a case_type that would
    otherwise route to wrong_module/wrong_intent/cross_language_confusion,
    the handler-reject classification wins (it's the more specific signal:
    we know the failsafe fired, that's more useful than 'agent guessed wrong')."""
    row = {
        "outcome": "wrong_file",
        "case_type": "same_name_different_module",
        "error_msg": "symbol 'X' not found in foo.py at HEAD — #280",
    }
    assert classify_failure_mode(row) == "hallucinated_symbol"
