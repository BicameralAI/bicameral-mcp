"""Parity gate for the tags-query substrate against per-language walkers.

Load-bearing risk mitigation for the Path B design recorded in #367. The
substrate (``code_locator.indexing.tags_extractor``) is the data-driven
extractor that powers Elixir today and is targeted to replace bespoke
walkers for Python/Go/Rust per the #399 shadow-mode rollout. Each
language progression depends on this gate proving:

    walker_symbols ⊆ tags_extractor_symbols    (restricted to walker vocab)

If the substrate misses any symbol the walker captures, the gate fails
and the PR cannot merge. This is the assertion that converts "untested"
→ "validated against existing ground truth in the same PR."

The reverse direction (tags-only-extra) is NOT asserted: tags.scm
captures module-level constants, type aliases, and macros that walkers
explicitly skip. Substrate-as-superset is by design — those extras are
filtered out before downstream consumers by ``_KIND_TO_TYPE`` and the
``walker_vocab`` restriction below.

## Coverage

- **Python** (#367 / #400): in-repo fixture files exercising handler,
  ledger, and test shapes.
- **Go** (#399 Stage B): clone-on-demand corpus from
  ``kubernetes/kubernetes@v1.30.0`` (``staging/src/k8s.io/api/core/v1``)
  and ``gohugoio/hugo@v0.130.0`` (``hugolib``). Sourced live via
  ``tests/_oss_corpus.py``'s sparse-clone helper — see that module's
  docstring for the "why clone-on-demand" rationale and bandwidth
  budget.
- **Rust** (#399 Stage B, Rust half): clone-on-demand corpus from
  ``BurntSushi/ripgrep@14.1.1`` (``crates/core``) and
  ``rust-lang/cargo@0.81.0`` (``src/cargo/core``). Same delivery
  pipeline as Go.

## When this gate fails

1. Read the failure message — every walker-only (name, type) pair is
   listed per file.
2. If the walker is right and tags missed something, the substrate has
   a bug. Likely culprits: missing entry in ``_KIND_TO_TYPE``, broken
   ``parent_qn`` ancestor walk, or an upstream tags.scm gap.
3. If the walker is wrong (rare), update test expectations AND file a
   separate issue documenting the walker bug.

NEVER skip this gate by adding ``@pytest.mark.xfail`` — the substrate
shipping with Elixir relying on it makes correctness load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tree_sitter as ts
import tree_sitter_go as tsgo
import tree_sitter_python as tsp
import tree_sitter_rust as tsrs

from code_locator.indexing.symbol_extractor import extract_symbols_from_content
from code_locator.indexing.tags_extractor import (
    extract_defs_via_tags,
    load_tags_query_text,
)
from tests._oss_corpus import OssSource, discover_files, sparse_clone

REPO_ROOT = Path(__file__).resolve().parent.parent

# ── Python corpus (in-repo fixtures, #367 / #400) ───────────────────

# Real Python files from this repo. Pick a diverse set: handler with many
# classes, ledger with many functions + nested helpers, test file with
# nested fixture classes, simple module with helpers only.
PY_FIXTURE_FILES = [
    "handlers/preflight.py",
    "handlers/remove_source.py",
    "ledger/queries.py",
    "tests/test_remove_source.py",
    "code_locator/indexing/tags_extractor.py",  # dogfood the new file
    "code_locator/indexing/symbol_extractor.py",  # large file with all walkers
]

# Python walker emits these type values. Filter substrate output to this
# vocabulary before comparison — substrate also captures module-level
# constants the walker skips, which is by design (#367 spec).
PY_WALKER_VOCAB = {"function", "class", "method"}

# ── Go corpus (clone-on-demand, #399 Stage B) ───────────────────────

# kubernetes/kubernetes — large idiomatic Go corpus; covers structs,
# interfaces, methods on pointer receivers, embedded types. Sparse
# path: the canonical core API types live at
# staging/src/k8s.io/api/core/v1 (the path the #399 issue body referred
# to as "pkg/api/v1" in modern k8s is a thin wrapper over this; the
# actual types live in staging/).
GO_SOURCE_K8S = OssSource(
    repo="kubernetes/kubernetes",
    ref="v1.30.0",
    sparse_path="staging/src/k8s.io/api/core/v1",
)

# gohugoio/hugo — smaller idiomatic codebase; covers single-file
# packages, type aliases, function types.
GO_SOURCE_HUGO = OssSource(
    repo="gohugoio/hugo",
    ref="v0.130.0",
    sparse_path="hugolib",
)

# Max files per source (the #399 plan targets ~20 from k8s + ~5 from
# hugo). Sort-then-cap = deterministic file selection across runs.
GO_MAX_FILES_K8S = 20
GO_MAX_FILES_HUGO = 5

# Test files have unrepresentative shapes (table-driven test funcs etc.)
# and add noise without adding coverage. Exclude them.
GO_EXCLUDE_GLOBS = ("*_test.go",)

# Go walker (symbol_extractor._extract_go_defs) emits "function" for
# both top-level funcs and methods, and "class" for struct/interface
# type_specs. It does NOT emit a "method" vocabulary value (methods
# fold into "function" — see locked #367 mapping decision).
GO_WALKER_VOCAB = {"function", "class"}

# ── Rust corpus (clone-on-demand, #399 Stage B) ─────────────────────

# BurntSushi/ripgrep — focused, well-organized Rust at scale. Covers
# traits, generics, impl blocks, lifetimes, derive macros. The
# ``crates/core`` directory holds the CLI front-end and flag parser —
# a behavior-heavy slice with many impls and trait implementations
# (the densest method-bearing surface in the repo).
RUST_SOURCE_RIPGREP = OssSource(
    repo="BurntSushi/ripgrep",
    ref="14.1.1",
    sparse_path="crates/core",
)

# rust-lang/cargo — real-world Rust workspace; covers complex trait
# hierarchies and cross-module patterns. ``src/cargo/core`` is the
# package-graph + dependency-resolution core, a struct-heavy slice
# that complements ripgrep's behavior-heavy CLI code.
RUST_SOURCE_CARGO = OssSource(
    repo="rust-lang/cargo",
    ref="0.81.0",
    sparse_path="src/cargo/core",
)

# Max files per source (#399 plan targets ~20 from ripgrep + ~5 from
# cargo). Sort-then-cap = deterministic file selection across runs.
RUST_MAX_FILES_RIPGREP = 20
RUST_MAX_FILES_CARGO = 5

# Rust convention is inline ``#[cfg(test)] mod tests`` rather than
# separate ``*_test.rs`` files (unlike Go), so this exclude rarely
# fires — kept as defensive parity with the Go branch in case a future
# crate adopts a different convention.
RUST_EXCLUDE_GLOBS = ("*_test.rs",)

# Rust walker (symbol_extractor._extract_rust_defs) emits "class" for
# ``struct_item`` / ``enum_item`` / ``trait_item`` and "function" for
# ``function_item``. The substrate's tags.scm captures additional
# kinds — ``macro`` (no walker emits), ``module``, ``interface``
# (trait) — which fall outside the walker vocab below and are
# substrate-only extras (allowed by design, #367 spec).
RUST_WALKER_VOCAB = {"function", "class"}


# ── Tree-sitter language/parser fixtures ────────────────────────────


@pytest.fixture(scope="module")
def py_language() -> ts.Language:
    return ts.Language(tsp.language())


@pytest.fixture(scope="module")
def py_parser(py_language: ts.Language) -> ts.Parser:
    return ts.Parser(py_language)


@pytest.fixture(scope="module")
def py_tags_query_text() -> str:
    text = load_tags_query_text("tree_sitter_python")
    if text is None:
        pytest.skip(
            "tree_sitter_python is not installed or doesn't ship queries/tags.scm; "
            "the parity gate cannot run on this environment."
        )
    return text


@pytest.fixture(scope="module")
def go_language() -> ts.Language:
    return ts.Language(tsgo.language())


@pytest.fixture(scope="module")
def go_parser(go_language: ts.Language) -> ts.Parser:
    return ts.Parser(go_language)


@pytest.fixture(scope="module")
def go_tags_query_text() -> str:
    text = load_tags_query_text("tree_sitter_go")
    if text is None:
        pytest.skip(
            "tree_sitter_go is not installed or doesn't ship queries/tags.scm; "
            "the parity gate cannot run on this environment."
        )
    return text


@pytest.fixture(scope="module")
def rust_language() -> ts.Language:
    return ts.Language(tsrs.language())


@pytest.fixture(scope="module")
def rust_parser(rust_language: ts.Language) -> ts.Parser:
    return ts.Parser(rust_language)


@pytest.fixture(scope="module")
def rust_tags_query_text() -> str:
    text = load_tags_query_text("tree_sitter_rust")
    if text is None:
        pytest.skip(
            "tree_sitter_rust is not installed or doesn't ship queries/tags.scm; "
            "the parity gate cannot run on this environment."
        )
    return text


# ── Go corpus fixture (session-scoped: one clone per pytest run) ────


@pytest.fixture(scope="session")
def go_corpus_files(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """Materialize the Go corpus once per session via sparse clones.

    Two sparse clones (k8s + hugo) into ``tmp_path_factory.mktemp(...)``.
    Returns the discovered ``.go`` file list (test files excluded, capped
    per source). The clones live for the duration of the pytest session
    and are auto-cleaned by pytest's tmp_path machinery.

    On clone failure (network, missing tag, no git) the underlying
    helper calls ``pytest.fail`` — never silent-skip. See
    ``tests/_oss_corpus.py`` for the failure-loud rationale.
    """
    cache_root = tmp_path_factory.mktemp("oss_corpus_go")
    files: list[Path] = []

    for source, max_files in (
        (GO_SOURCE_K8S, GO_MAX_FILES_K8S),
        (GO_SOURCE_HUGO, GO_MAX_FILES_HUGO),
    ):
        clone_dir = cache_root / source.cache_dirname()
        sparse_clone(source, clone_dir)
        files.extend(
            discover_files(
                clone_dir,
                source,
                extension="go",
                exclude_globs=GO_EXCLUDE_GLOBS,
                max_files=max_files,
            )
        )

    return files


# ── Rust corpus fixture (session-scoped: one clone per pytest run) ──


@pytest.fixture(scope="session")
def rust_corpus_files(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    """Materialize the Rust corpus once per session via sparse clones.

    Two sparse clones (ripgrep + cargo) into ``tmp_path_factory.mktemp(...)``.
    Same failure model as the Go corpus fixture — see ``tests/_oss_corpus.py``
    for the fail-loud rationale.
    """
    cache_root = tmp_path_factory.mktemp("oss_corpus_rust")
    files: list[Path] = []

    for source, max_files in (
        (RUST_SOURCE_RIPGREP, RUST_MAX_FILES_RIPGREP),
        (RUST_SOURCE_CARGO, RUST_MAX_FILES_CARGO),
    ):
        clone_dir = cache_root / source.cache_dirname()
        sparse_clone(source, clone_dir)
        files.extend(
            discover_files(
                clone_dir,
                source,
                extension="rs",
                exclude_globs=RUST_EXCLUDE_GLOBS,
                max_files=max_files,
            )
        )

    return files


# ── Parity tests ────────────────────────────────────────────────────


@pytest.mark.parametrize("rel_path", PY_FIXTURE_FILES)
def test_python_walker_subset_of_tags_extractor(
    rel_path: str,
    py_language: ts.Language,
    py_parser: ts.Parser,
    py_tags_query_text: str,
) -> None:
    """Per-file Python parity check. Iterates one file at a time so
    failures point at the exact fixture that regressed."""
    src = REPO_ROOT / rel_path
    if not src.exists():
        pytest.skip(f"fixture {rel_path} not present in this checkout")
    content = src.read_text(encoding="utf-8")
    code_bytes = content.encode("utf-8")

    walker_records = extract_symbols_from_content(content, "python", rel_path)
    walker_set = {(r.name, r.type) for r in walker_records if r.type in PY_WALKER_VOCAB}

    tree = py_parser.parse(code_bytes)
    substrate_records = extract_defs_via_tags(
        py_language, tree, code_bytes, rel_path, py_tags_query_text
    )
    substrate_set = {(r.name, r.type) for r in substrate_records if r.type in PY_WALKER_VOCAB}

    missing = walker_set - substrate_set
    assert not missing, (
        f"Substrate parity gate FAIL on {rel_path}: walker found "
        f"{len(missing)} symbol(s) the substrate missed.\n\n"
        f"Walker-only symbols (would silently disappear if walker retired):\n"
        + "\n".join(f"  - {n} (type={t})" for n, t in sorted(missing))
        + f"\n\nWalker total: {len(walker_set)}, Substrate total (filtered): "
        f"{len(substrate_set)}.\n\n"
        "Most likely (a) substrate bug in capture interpretation, (b) "
        "_KIND_TO_TYPE missing a kind, or (c) walker emits symbols a "
        "tags.scm pattern doesn't cover. Fix the substrate before "
        "merging — Elixir relies on this code path."
    )


def test_parity_gate_runs_on_at_least_three_python_fixtures() -> None:
    """Sanity: if all fixtures get skipped (e.g. repo restructure), the
    gate silently provides zero Python coverage. Fail loudly when too
    few real fixtures are reachable."""
    present = sum(1 for p in PY_FIXTURE_FILES if (REPO_ROOT / p).exists())
    assert present >= 3, (
        f"Only {present}/{len(PY_FIXTURE_FILES)} Python fixture files "
        f"present — the parity gate's coverage is too thin to defend the "
        f"substrate. Either restore the missing files or update "
        f"PY_FIXTURE_FILES with new diverse Python samples."
    )


def test_go_walker_subset_of_substrate(
    go_corpus_files: list[Path],
    go_language: ts.Language,
    go_parser: ts.Parser,
    go_tags_query_text: str,
) -> None:
    """Go parity check — aggregates failures across the full Go corpus
    so one clone runs once per session, but per-file violations are
    still surfaced individually in the failure message.

    Restricted to ``GO_WALKER_VOCAB`` (``{"function", "class"}``)
    because the Go walker doesn't emit a ``method`` value (methods
    fold into ``function`` per the locked #367 vocabulary mapping).

    See module docstring for the "what to do if this fails" runbook.
    """
    assert go_corpus_files, (
        "Go corpus fixture produced zero files; investigate _oss_corpus.discover_files"
    )

    violations: list[tuple[Path, set[tuple[str, str]], int, int]] = []
    total_walker = 0
    total_substrate = 0

    for fp in go_corpus_files:
        content = fp.read_text(encoding="utf-8")
        code_bytes = content.encode("utf-8")
        rel = str(fp)

        walker_records = extract_symbols_from_content(content, "go", rel)
        walker_set = {(r.name, r.type) for r in walker_records if r.type in GO_WALKER_VOCAB}

        tree = go_parser.parse(code_bytes)
        substrate_records = extract_defs_via_tags(
            go_language, tree, code_bytes, rel, go_tags_query_text
        )
        substrate_set = {(r.name, r.type) for r in substrate_records if r.type in GO_WALKER_VOCAB}

        total_walker += len(walker_set)
        total_substrate += len(substrate_set)

        missing = walker_set - substrate_set
        if missing:
            violations.append((fp, missing, len(walker_set), len(substrate_set)))

    if violations:
        # Format every violation in the same failure message — debugging
        # starts from a single read of one assertion error, not from
        # re-running with --tb=long across N parametrized cases.
        blocks = []
        for fp, missing, w_total, s_total in violations:
            try:
                pretty = fp.relative_to(fp.parents[3])  # strip up to clone root
            except (ValueError, IndexError):
                pretty = fp
            blocks.append(
                f"FILE: {pretty}\n"
                f"  walker total: {w_total}, substrate total (filtered): {s_total}\n"
                f"  walker-only symbols (substrate gap):\n"
                + "\n".join(f"    - {n} (type={t})" for n, t in sorted(missing))
            )
        pytest.fail(
            f"Go parity gate FAIL — {len(violations)}/{len(go_corpus_files)} "
            f"corpus file(s) had walker-only symbols the substrate missed.\n\n"
            + "\n\n".join(blocks)
            + "\n\nAggregate: walker emitted "
            f"{total_walker} symbols, substrate emitted {total_substrate} "
            "(filtered to walker vocab).\n\n"
            "Most likely (a) substrate bug in capture interpretation, (b) "
            "_KIND_TO_TYPE missing a kind, or (c) walker emits symbols a "
            "tags.scm pattern doesn't cover. Fix the substrate before "
            "merging — Elixir relies on this code path and Stages C-E of "
            "#399 cannot proceed without this guarantee."
        )


def test_rust_walker_subset_of_substrate(
    rust_corpus_files: list[Path],
    rust_language: ts.Language,
    rust_parser: ts.Parser,
    rust_tags_query_text: str,
) -> None:
    """Rust parity check — same shape as the Go test (aggregating loop,
    single rich failure message). Restricted to ``RUST_WALKER_VOCAB``
    (``{"function", "class"}``) since the Rust walker doesn't emit
    ``method`` (impl methods are ``function_item`` in tree-sitter-rust
    and fold into the walker's ``function`` vocabulary).

    See module docstring for the "what to do if this fails" runbook.
    """
    assert rust_corpus_files, (
        "Rust corpus fixture produced zero files; investigate _oss_corpus.discover_files"
    )

    violations: list[tuple[Path, set[tuple[str, str]], int, int]] = []
    total_walker = 0
    total_substrate = 0

    for fp in rust_corpus_files:
        content = fp.read_text(encoding="utf-8")
        code_bytes = content.encode("utf-8")
        rel = str(fp)

        walker_records = extract_symbols_from_content(content, "rust", rel)
        walker_set = {(r.name, r.type) for r in walker_records if r.type in RUST_WALKER_VOCAB}

        tree = rust_parser.parse(code_bytes)
        substrate_records = extract_defs_via_tags(
            rust_language, tree, code_bytes, rel, rust_tags_query_text
        )
        substrate_set = {(r.name, r.type) for r in substrate_records if r.type in RUST_WALKER_VOCAB}

        total_walker += len(walker_set)
        total_substrate += len(substrate_set)

        missing = walker_set - substrate_set
        if missing:
            violations.append((fp, missing, len(walker_set), len(substrate_set)))

    if violations:
        blocks = []
        for fp, missing, w_total, s_total in violations:
            try:
                pretty = fp.relative_to(fp.parents[3])
            except (ValueError, IndexError):
                pretty = fp
            blocks.append(
                f"FILE: {pretty}\n"
                f"  walker total: {w_total}, substrate total (filtered): {s_total}\n"
                f"  walker-only symbols (substrate gap):\n"
                + "\n".join(f"    - {n} (type={t})" for n, t in sorted(missing))
            )
        pytest.fail(
            f"Rust parity gate FAIL — {len(violations)}/{len(rust_corpus_files)} "
            f"corpus file(s) had walker-only symbols the substrate missed.\n\n"
            + "\n\n".join(blocks)
            + "\n\nAggregate: walker emitted "
            f"{total_walker} symbols, substrate emitted {total_substrate} "
            "(filtered to walker vocab).\n\n"
            "Most likely (a) substrate bug in capture interpretation, (b) "
            "_KIND_TO_TYPE missing a kind, or (c) walker emits symbols a "
            "tags.scm pattern doesn't cover. Fix the substrate before "
            "merging — Elixir relies on this code path and Stages C-E of "
            "#399 cannot proceed without this guarantee."
        )
