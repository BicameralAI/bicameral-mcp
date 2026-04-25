"""Tests for ledger/ast_diff.py — V1 B1 cosmetic-change classifier.

Whitelist tests: changes that ``is_cosmetic_change`` MUST classify True.
Anti-whitelist tests: changes that MUST classify False even though they
"look" mechanical — variable renames, trailing commas, comment edits,
docstring edits, tool directives. False positives in this layer would
bias the V2 caller-LLM verdict prompt toward "looks fine" on
behaviorally-different code.
"""
from __future__ import annotations

import pytest

from ledger.ast_diff import is_cosmetic_change


# ── Whitelist: must return True ─────────────────────────────────────


def test_identical_bytes():
    assert is_cosmetic_change("def f(): return 1", "def f(): return 1", "python") is True


def test_intra_line_horizontal_whitespace_python():
    """Spaces tightened around an operator — token stream unchanged."""
    before = "def f(x):\n    return x+1\n"
    after = "def f(x):\n    return x + 1\n"
    assert is_cosmetic_change(before, after, "python") is True


def test_blank_line_between_statements_python():
    before = "def f():\n    a()\n    b()\n"
    after = "def f():\n    a()\n\n    b()\n"
    assert is_cosmetic_change(before, after, "python") is True


def test_trailing_whitespace_stripped_python():
    before = "def f():    \n    return 1   \n"
    after = "def f():\n    return 1\n"
    assert is_cosmetic_change(before, after, "python") is True


def test_indent_width_change_python():
    """Two-space vs four-space indent — same logical block structure."""
    before = "def f():\n  return 1\n"
    after = "def f():\n    return 1\n"
    assert is_cosmetic_change(before, after, "python") is True


def test_intra_line_whitespace_javascript():
    before = "function f(){return 1+2;}"
    after = "function f() { return 1 + 2; }"
    assert is_cosmetic_change(before, after, "javascript") is True


# ── Anti-whitelist: must return False ───────────────────────────────


def test_variable_rename_python():
    """Renames are observable via kwargs/reflection/ORM — never cosmetic."""
    before = "def f(x):\n    return x + 1\n"
    after = "def f(y):\n    return y + 1\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_function_rename_python():
    before = "def calculateDiscount(x):\n    return x * 0.1\n"
    after = "def computeDiscount(x):\n    return x * 0.1\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_trailing_comma_added_python():
    """`(x,)` is a 1-tuple, `(x)` is a parenthesized expression."""
    before = "x = (1)\n"
    after = "x = (1,)\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_line_comment_edited_python():
    """Comments carry tool directives like # type: ignore — never cosmetic."""
    before = "def f():\n    # old comment\n    return 1\n"
    after = "def f():\n    # new comment\n    return 1\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_type_ignore_added_python():
    before = "x = something()\n"
    after = "x = something()  # type: ignore\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_noqa_added_python():
    before = "import sys\n"
    after = "import sys  # noqa: F401\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_docstring_edited_python():
    """Docstrings are observable via __doc__ — never cosmetic."""
    before = 'def f():\n    """Original."""\n    return 1\n'
    after = 'def f():\n    """Updated."""\n    return 1\n'
    assert is_cosmetic_change(before, after, "python") is False


def test_string_literal_edited_python():
    before = 'route = "/api/v1/users"\n'
    after = 'route = "/api/v2/users"\n'
    assert is_cosmetic_change(before, after, "python") is False


def test_import_reorder_python():
    before = "import os\nimport sys\n"
    after = "import sys\nimport os\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_ts_ignore_added_typescript():
    before = "const x = something();\n"
    after = "// @ts-ignore\nconst x = something();\n"
    assert is_cosmetic_change(before, after, "typescript") is False


def test_block_restructured_python():
    """Re-indenting moves a statement out of an if block — semantics change."""
    before = "if x:\n    a()\n    b()\n"
    after = "if x:\n    a()\nb()\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_statement_added_python():
    before = "def f():\n    return 1\n"
    after = "def f():\n    log()\n    return 1\n"
    assert is_cosmetic_change(before, after, "python") is False


# ── Failure modes — fail safe ────────────────────────────────────────


def test_unsupported_language_returns_false():
    assert is_cosmetic_change("foo", "bar", "ruby") is False
    assert is_cosmetic_change("foo", "bar", "elixir") is False
    assert is_cosmetic_change("foo", "bar", "") is False


def test_parse_error_returns_false():
    """Syntactically broken code → don't claim cosmetic."""
    before = "def f(:\n  pass\n"  # broken
    after = "def f():\n  pass\n"
    assert is_cosmetic_change(before, after, "python") is False


def test_jsx_routes_through_javascript():
    """JSX/TSX fall back to javascript/typescript per LANGUAGE_FALLBACK.

    Inputs must differ in bytes (otherwise the early-return at the top of
    is_cosmetic_change short-circuits and the fallback path is never
    exercised). Whitespace-only diff keeps the expected outcome True
    while forcing the LANGUAGE_FALLBACK['jsx'] → 'javascript' resolution
    and the _get_parser code path to actually run.
    """
    before = "const X = () => <div>hi</div>"
    after = "const  X  =  () => <div>hi</div>"  # extra spaces in the JS portion
    assert is_cosmetic_change(before, after, "jsx") is True
