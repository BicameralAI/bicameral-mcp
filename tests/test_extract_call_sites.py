"""Phase 4 / Phase 2 (#61) — call-site extractor tests.

Covers ``code_locator.indexing.call_site_extractor.extract_call_sites``
across all 7 supported languages. The classifier's
``_signal_no_new_calls`` (15% of the cosmetic-vs-semantic score) depends
on this primitive returning a deterministic ``set[str]`` of called
callable names.

Failure isolation: parser unavailable / parse failure / unsupported
language must all return ``set()`` (never raise).
"""

from __future__ import annotations

import pytest

from code_locator.indexing.call_site_extractor import extract_call_sites


# ── Per-language happy-path tests ────────────────────────────────────


def test_extract_call_sites_python() -> None:
    code = """
def f():
    bar()
    obj.method()
    A().b()
    print("hello")
"""
    calls = extract_call_sites(code, "python")
    # Member-access callees collapse to the trailing identifier.
    assert "bar" in calls
    assert "method" in calls
    assert "b" in calls
    assert "print" in calls


def test_extract_call_sites_javascript() -> None:
    code = """
function f() {
    bar();
    obj.method();
    new Foo();
    console.log("hi");
}
"""
    calls = extract_call_sites(code, "javascript")
    assert "bar" in calls
    assert "method" in calls
    assert "log" in calls
    # `new Foo()` is a `new_expression` in JS tree-sitter, not call_expression;
    # we don't claim to capture it (constructor invocation is a distinct concern).


def test_extract_call_sites_typescript() -> None:
    code = """
function f<T>(x: T): T {
    return identity<T>(x);
}
const y = wrap(42);
"""
    calls = extract_call_sites(code, "typescript")
    assert "identity" in calls
    assert "wrap" in calls


def test_extract_call_sites_go() -> None:
    code = """
package main

import "fmt"

func F() {
    fmt.Println("hi")
    Helper()
    obj.Method()
}
"""
    calls = extract_call_sites(code, "go")
    assert "Println" in calls
    assert "Helper" in calls
    assert "Method" in calls


def test_extract_call_sites_rust() -> None:
    code = """
fn main() {
    println!("hi");
    helper();
    let x = std::cmp::max(1, 2);
    obj.method();
}
"""
    calls = extract_call_sites(code, "rust")
    # `println!` is a macro_invocation, not a call_expression — skipped.
    assert "helper" in calls
    assert "max" in calls          # std::cmp::max → "max" (last identifier)
    assert "method" in calls


def test_extract_call_sites_java() -> None:
    code = """
class Demo {
    void f() {
        System.out.println("hi");
        helper();
        obj.method();
    }
}
"""
    calls = extract_call_sites(code, "java")
    assert "println" in calls
    assert "helper" in calls
    assert "method" in calls


def test_extract_call_sites_c_sharp() -> None:
    """F3 + F4: explicit ``c_sharp`` (underscore) input flows end-to-end."""
    code = """
class Demo {
    void F() {
        Console.WriteLine("hi");
        Helper();
        obj.Method();
    }
}
"""
    calls = extract_call_sites(code, "c_sharp")
    assert "WriteLine" in calls
    assert "Helper" in calls
    assert "Method" in calls


# ── Failure-mode tests ──────────────────────────────────────────────


def test_extract_call_sites_returns_empty_for_unparseable_input() -> None:
    """Garbled input with no recoverable AST returns an empty set
    rather than raising."""
    # Tree-sitter is forgiving — most input parses to *some* tree —
    # but null bytes and binary noise won't produce call expressions.
    calls = extract_call_sites("\x00\x01\x02 not python at all }}}", "python")
    assert calls == set()


def test_extract_call_sites_returns_empty_for_unsupported_language() -> None:
    """Unsupported language returns an empty set rather than raising.

    Aligns with the classifier's contract: 0.5 (unknown) signal weight
    on empty extraction. The ``no_new_calls`` signal in
    ``codegenome.drift_classifier`` falls back to 0.5 on empty old or
    empty new, so unsupported languages don't accidentally vote
    "cosmetic" via subset-of-empty-is-empty.
    """
    assert extract_call_sites("def f(): bar()", "ruby") == set()
    assert extract_call_sites("def f(): bar()", "") == set()


def test_extract_call_sites_empty_content() -> None:
    """Empty source returns empty set on every supported language."""
    for lang in ("python", "javascript", "typescript", "go", "rust", "java", "c_sharp"):
        assert extract_call_sites("", lang) == set(), f"empty content, {lang}"
