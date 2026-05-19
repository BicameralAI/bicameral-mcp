"""Substrate-side smoke test for Go symbol extraction (#399 Stage A).

Validates that the generic tags-query substrate emits Go struct and
interface declarations as ``SymbolRecord.type="class"`` via the
``"type" → "class"`` mapping in
``code_locator.indexing.tags_extractor._KIND_TO_TYPE``. Without that
mapping, the upstream ``tree-sitter-go/queries/tags.scm``'s
``@definition.type`` captures get silently dropped — but the Go walker
(``_extract_go_defs``) DOES emit struct/interface as ``type='class'``,
so the parity gate would fail on any Go file. This test pins the fix
ahead of the Stage B full parity gate extension.

Inline Go fixture is intentional — the full per-language corpus
expansion lives in Stage B per the #399 plan. This Stage A test only
needs to demonstrate that the new mapping closes the gap; an inline
fixture is sufficient and keeps the test independent of any future
corpus changes.

Companion to ``tests/test_extract_elixir.py`` (#367 — Elixir runs
through the same substrate) and ``tests/test_tags_extractor_parity.py``
(#400 — Python walker ⊆ substrate parity gate).
"""

from __future__ import annotations

import pytest
import tree_sitter as ts
import tree_sitter_go as tsgo

from code_locator.indexing.tags_extractor import (
    extract_defs_via_tags,
    load_tags_query_text,
)

# Phoenix-shaped Go fixture — covers the three @definition.<kind>
# captures the upstream tags.scm produces (function/method/type) plus
# a regular package-level function to anchor the function path.
_FIXTURE = """\
package accounts

import "fmt"

type User struct {
    ID   int
    Name string
}

type Repository interface {
    Get(id int) (*User, error)
    List() ([]*User, error)
}

func GetUser(id int) (*User, error) {
    fmt.Println("getting user", id)
    return nil, nil
}

func (u *User) DisplayName() string {
    return u.Name
}
"""


@pytest.fixture(scope="module")
def go_language() -> ts.Language:
    return ts.Language(tsgo.language())


@pytest.fixture(scope="module")
def go_parser(go_language: ts.Language) -> ts.Parser:
    return ts.Parser(go_language)


@pytest.fixture(scope="module")
def go_tags_query() -> str:
    text = load_tags_query_text("tree_sitter_go")
    if text is None:
        pytest.skip("tree_sitter_go is not installed or doesn't ship queries/tags.scm")
    return text


@pytest.fixture(scope="module")
def go_records(go_language, go_parser, go_tags_query) -> list:
    code = _FIXTURE.encode("utf-8")
    tree = go_parser.parse(code)
    return extract_defs_via_tags(go_language, tree, code, "fixture.go", go_tags_query)


def test_struct_captured_as_class(go_records: list) -> None:
    """Go ``type User struct {…}`` should produce a SymbolRecord with
    name='User', type='class' via the new ``@definition.type → class``
    mapping. If this fails, the Stage A mapping fix regressed."""
    user = next((r for r in go_records if r.name == "User"), None)
    assert user is not None, (
        f"User struct missing from substrate output. Got: {[(r.name, r.type) for r in go_records]}"
    )
    assert user.type == "class", (
        f"User struct should map to type='class' (matches Go walker output); "
        f"got type='{user.type}'. Likely the '_KIND_TO_TYPE['type']' mapping "
        f"is missing or wrong."
    )


def test_interface_captured_as_class(go_records: list) -> None:
    """Go ``type Repository interface {…}`` should also produce
    type='class' — both struct_type and interface_type go through the
    same ``type_spec → @definition.type`` capture in tree-sitter-go's
    tags.scm. The walker treats both as classes; substrate must agree."""
    repo = next((r for r in go_records if r.name == "Repository"), None)
    assert repo is not None, "Repository interface missing from substrate output"
    assert repo.type == "class", (
        f"Repository interface should map to type='class'; got type='{repo.type}'."
    )


def test_function_captured_as_function(go_records: list) -> None:
    """Top-level Go func → @definition.function → type='function'.
    This was already covered by the existing mapping but pin it so a
    refactor doesn't silently break the baseline."""
    fn = next((r for r in go_records if r.name == "GetUser"), None)
    assert fn is not None, "GetUser function missing from substrate output"
    assert fn.type == "function", f"GetUser should map to type='function'; got type='{fn.type}'."


def test_method_captured_as_function(go_records: list) -> None:
    """Go ``func (u *User) DisplayName()`` → @definition.method →
    type='function'. The method kind folds into the walker's
    'function' vocabulary per the locked #367 mapping decision."""
    method = next((r for r in go_records if r.name == "DisplayName"), None)
    assert method is not None, "DisplayName method missing from substrate output"
    assert method.type == "function", (
        f"DisplayName method should map to type='function'; got type='{method.type}'."
    )


def test_no_unexpected_extras(go_records: list) -> None:
    """The substrate should emit exactly four records on this fixture
    (User, Repository, GetUser, DisplayName). If a new record appears,
    upstream tree-sitter-go's tags.scm added a new ``@definition.*``
    capture — investigate before merging this PR (the new kind may need
    a ``_KIND_TO_TYPE`` mapping or may be a substrate-only extra)."""
    names = sorted(r.name for r in go_records)
    assert names == ["DisplayName", "GetUser", "Repository", "User"], (
        f"Unexpected substrate output on Go fixture: {names}. "
        f"Audit whether tree-sitter-go's tags.scm added new captures."
    )
