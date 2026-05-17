"""Parity gate for the tags-query substrate against the Python walker (#367).

Load-bearing risk mitigation for the Path B design recorded in #367. The
substrate (``code_locator.indexing.tags_extractor``) is introduced here
for Elixir, but we have no Elixir walker to compare it against. To
validate the substrate's correctness BEFORE shipping it, this test
shadow-runs it against the existing Python walker output on real repo
files. The empirical pre-flight (#399 research) showed that for Python,
walker symbols are a strict subset of tags-query output. This gate locks
that property in.

Contract — for every Python fixture file, restricted to the walker's
symbol vocabulary (``{"function", "class", "method"}``):

    walker_symbols ⊆ tags_extractor_symbols

If the substrate misses a symbol the walker captures, the gate fails and
the PR cannot merge. This is the assertion that converts "untested first
instance" → "validated against existing ground truth in this same PR."

The reverse direction (tags-only-extra) is NOT asserted: tags.scm
captures module-level constants the walker explicitly skips. Allowing
the substrate to be a superset is by design — those extras are filtered
out by ``_KIND_TO_TYPE`` not mapping ``constant`` to a walker type.

The fixture corpus is sourced from the bicameral-mcp repo itself (handler
files, ledger files, test files — diverse Python shapes). Adding more
fixtures sharpens the gate; removing them weakens it.

When this gate fails:
  1. Read the diff between walker and substrate outputs printed in the
     failure message.
  2. If the walker is right and tags missed something, the substrate has
     a bug (likely in ``_KIND_TO_TYPE`` or parent_qn computation). Fix
     it before merging.
  3. If the walker is wrong (rare), update the test expectations AND
     file a separate issue documenting the walker bug.

NEVER skip this gate by adding ``@pytest.mark.xfail`` — the substrate
shipping with Elixir relying on it makes correctness load-bearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tree_sitter as ts
import tree_sitter_python as tsp

from code_locator.indexing.symbol_extractor import extract_symbols_from_content
from code_locator.indexing.tags_extractor import (
    extract_defs_via_tags,
    load_tags_query_text,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Real Python files from this repo. Pick a diverse set: handler with many
# classes, ledger with many functions + nested helpers, test file with
# nested fixture classes, simple module with helpers only.
FIXTURE_FILES = [
    "handlers/preflight.py",
    "handlers/remove_source.py",
    "ledger/queries.py",
    "tests/test_remove_source.py",
    "code_locator/indexing/tags_extractor.py",  # dogfood the new file
    "code_locator/indexing/symbol_extractor.py",  # large file with all walkers
]

# Walker emits these type values for Python. Filter the substrate's
# output to the same set before comparison — substrate captures extra
# module-level constants the walker skips, which is by design (#367 spec).
WALKER_VOCAB = {"function", "class", "method"}


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


@pytest.mark.parametrize("rel_path", FIXTURE_FILES)
def test_python_walker_subset_of_tags_extractor(
    rel_path: str,
    py_language: ts.Language,
    py_parser: ts.Parser,
    py_tags_query_text: str,
) -> None:
    """Walker symbols (name, type) ⊆ substrate symbols (name, type) on real
    Python files, after filtering substrate output to walker vocabulary.

    If this fails, the substrate has a bug — most likely a missing case in
    ``_KIND_TO_TYPE`` or a parent_qn computation error. Do NOT merge.
    """
    src = REPO_ROOT / rel_path
    if not src.exists():
        pytest.skip(f"fixture {rel_path} not present in this checkout")
    content = src.read_text(encoding="utf-8")
    code_bytes = content.encode("utf-8")

    # Walker output — what we ship today.
    walker_records = extract_symbols_from_content(content, "python", rel_path)
    walker_set = {(r.name, r.type) for r in walker_records if r.type in WALKER_VOCAB}

    # Substrate output, filtered to walker vocabulary.
    tree = py_parser.parse(code_bytes)
    substrate_records = extract_defs_via_tags(
        py_language, tree, code_bytes, rel_path, py_tags_query_text
    )
    substrate_set = {(r.name, r.type) for r in substrate_records if r.type in WALKER_VOCAB}

    missing = walker_set - substrate_set
    assert not missing, (
        f"Substrate parity gate FAIL on {rel_path}: walker found "
        f"{len(missing)} symbol(s) the substrate missed.\n\n"
        f"Walker-only symbols (would silently disappear if walker retired):\n"
        + "\n".join(f"  - {n} (type={t})" for n, t in sorted(missing))
        + f"\n\nWalker total: {len(walker_set)}, Substrate total (filtered): "
        f"{len(substrate_set)}.\n\n"
        "This means either (a) the substrate has a bug in capture "
        "interpretation, (b) _KIND_TO_TYPE is missing a kind, or (c) the "
        "walker is emitting symbols a tags.scm pattern doesn't cover. "
        "Fix the substrate before merging — Elixir relies on this code path."
    )


def test_parity_gate_runs_on_at_least_three_fixtures() -> None:
    """Sanity: if all fixtures get skipped (e.g. repo restructure), the
    gate silently provides zero coverage. Fail loudly when too few real
    fixtures are reachable."""
    present = sum(1 for p in FIXTURE_FILES if (REPO_ROOT / p).exists())
    assert present >= 3, (
        f"Only {present}/{len(FIXTURE_FILES)} fixture files present — the "
        f"parity gate's coverage is too thin to defend the substrate. "
        f"Either restore the missing files or update FIXTURE_FILES with "
        f"new diverse Python samples."
    )
