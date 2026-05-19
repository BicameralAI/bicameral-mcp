"""Regression test for Elixir symbol extraction via the tags-query substrate (#367).

End-to-end: parses ``tests/fixtures/elixir/sample_module.ex`` through the
public ``extract_symbols_from_content`` API (the same entry point the
production indexer uses), then asserts on the produced ``SymbolRecord``
shape:

- Exactly one ``@definition.module`` capture for ``MyApp.Accounts``,
  emitted with ``type="class"`` per the #367 type mapping.
- Five ``@definition.function`` captures (``get_user``,
  ``list_active_users``, ``find_by_email`` with its ``when`` guard,
  and the two ``active?`` clauses — emitting multiple rows per
  multi-clause function is intentional, see #367 Notes).
- Every function row has ``parent_qualified_name="MyApp.Accounts"`` and
  a fully-qualified name like ``MyApp.Accounts.get_user``.

Sociable per CLAUDE.md: real ``SymbolRecord`` dataclass, no
``MagicMock``. Test runs against the real ``tree-sitter-elixir`` grammar
package (pinned in ``pyproject.toml`` at ``>=0.3.5,<0.4``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from code_locator.indexing.symbol_extractor import extract_symbols_from_content

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "elixir" / "sample_module.ex"


@pytest.fixture(scope="module")
def records() -> list:
    if not FIXTURE.exists():
        pytest.skip(f"fixture missing: {FIXTURE}")
    return extract_symbols_from_content(
        FIXTURE.read_text(encoding="utf-8"),
        "elixir",
        "tests/fixtures/elixir/sample_module.ex",
    )


def test_module_captured_as_class(records: list) -> None:
    """``defmodule MyApp.Accounts`` produces one row with type='class'
    and qualified_name='MyApp.Accounts' (no parent — top-level)."""
    modules = [r for r in records if r.type == "class"]
    assert len(modules) == 1, f"expected 1 module, got {len(modules)}: {[m.name for m in modules]}"
    m = modules[0]
    assert m.name == "MyApp.Accounts"
    assert m.qualified_name == "MyApp.Accounts"
    assert m.parent_qualified_name == ""


def test_all_function_clauses_captured(records: list) -> None:
    """Five function-typed rows: get_user/1, list_active_users/0,
    find_by_email/1 (with when guard), and active?/1 × 2 clauses.
    Multi-clause functions deliberately emit one row per clause —
    faithful to source per the #367 design decision."""
    functions = [r for r in records if r.type == "function"]
    names = sorted(r.name for r in functions)
    # 2x active? (multi-clause) + find_by_email + get_user + list_active_users
    assert names == [
        "active?",
        "active?",
        "find_by_email",
        "get_user",
        "list_active_users",
    ], f"unexpected function set: {names}"


def test_functions_carry_module_parent(records: list) -> None:
    """Every function inside ``defmodule MyApp.Accounts`` carries
    parent_qualified_name='MyApp.Accounts' and a fully-qualified name
    like 'MyApp.Accounts.get_user' — the substrate's ancestor walk must
    resolve the enclosing module correctly."""
    functions = [r for r in records if r.type == "function"]
    for fn in functions:
        assert fn.parent_qualified_name == "MyApp.Accounts", (
            f"function {fn.name!r} has wrong parent: {fn.parent_qualified_name!r}"
        )
        assert fn.qualified_name == f"MyApp.Accounts.{fn.name}", (
            f"function {fn.name!r} has wrong qualified_name: {fn.qualified_name!r}"
        )


def test_multi_clause_active_lines_distinct(records: list) -> None:
    """The two ``active?`` clauses must surface as separate symbol rows
    with distinct ``start_line`` values — the substrate is faithful to
    source per #367 Notes (downstream consumers like validate_symbols
    handle N-row matches)."""
    active_rows = sorted((r for r in records if r.name == "active?"), key=lambda r: r.start_line)
    assert len(active_rows) == 2, f"expected 2 active? clauses, got {len(active_rows)}"
    assert active_rows[0].start_line != active_rows[1].start_line, (
        f"two active? clauses share start_line={active_rows[0].start_line}"
    )


def test_dispatch_returns_records_for_ex_extension() -> None:
    """End-to-end: extract_symbols (the file-path entrypoint) routes
    .ex/.exs files through the substrate. If EXTENSION_LANGUAGE or
    _LANG_PACKAGE_MAP didn't get wired, this fails."""
    from code_locator.indexing.symbol_extractor import extract_symbols

    records = extract_symbols(str(FIXTURE.resolve()), str(FIXTURE.parent.parent.parent))
    assert records, "dispatch returned no records — wiring is broken"
    types = {r.type for r in records}
    assert "class" in types and "function" in types
