"""Smoke tests for ``tests/eval_357_surrealql_coverage.py``.

Validates the audit script's discovery + render logic without paying the
full ~3-minute pytest-under-coverage cost. The CI step (``M_surrealql_coverage
(hard gate)``) is the actual gate; these tests just make sure the script
itself works.

Sociable per CLAUDE.md — uses real ``ast`` parsing of the real
``ledger/queries.py``, no MagicMock. The one thing not exercised here is
``run_coverage`` itself (would re-run the whole sociable test suite),
which the M_surrealql_coverage workflow step covers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "tests"))

from eval_357_surrealql_coverage import (  # noqa: E402
    analyze_coverage,
    discover_sociable_tests,
    discover_surrealql_functions,
    render_markdown,
)

# ── Discovery ──────────────────────────────────────────────────────────


def test_discover_finds_known_surrealql_functions():
    """The audit must find the same functions the issue body called out.
    If renaming or deleting any of these, the smoke test fails — forcing
    the audit's mental model to stay in sync with ``ledger/queries.py``."""
    funcs = discover_surrealql_functions()
    # Sample of functions known to issue raw SurrealQL (cited in #357 / #311)
    expected = {
        "get_ledger_revision",
        "upsert_decision",
        "upsert_code_region",
        "upsert_compliance_check",
        "get_all_decisions",
        "search_by_bm25",
    }
    missing = expected - funcs.keys()
    assert not missing, f"audit missed known SurrealQL-bearing functions: {missing}"


def test_discover_returns_valid_line_ranges():
    """Every discovered function must have ``start_line <= end_line`` and
    both > 0. Catches AST-mode bugs that would produce 0 or negative line
    numbers (would silently null out coverage analysis)."""
    funcs = discover_surrealql_functions()
    assert funcs, "audit produced empty function set"
    for fn, (start, end) in funcs.items():
        assert start > 0, f"{fn}: start_line={start}"
        assert end >= start, f"{fn}: end_line={end} < start_line={start}"


def test_discover_finds_sociable_tests():
    """At least the canonical reference patterns named in CLAUDE.md must
    show up as sociable: test_codegenome_continuity_service and
    test_sync_middleware."""
    tests = discover_sociable_tests()
    test_names = {t.name for t in tests}
    expected_sociable = {
        "test_codegenome_continuity_service.py",
        "test_codegenome_continuity_ledger.py",
    }
    missing = expected_sociable - test_names
    assert not missing, f"audit missed canonical sociable tests: {missing}"


def test_discover_excludes_mock_only_tests():
    """A test file that uses only MagicMock(LedgerClient) without any
    real-ledger marker must NOT be classified as sociable. Verifies the
    filter is rigorous against the failure mode #357 exists to prevent."""
    tests = discover_sociable_tests()
    test_names = {t.name for t in tests}
    # ``tests/test_ledger_mock_regression.py`` exists per the earlier
    # repo survey; check the actual file to see if it's marked sociable
    mock_path = _REPO_ROOT / "tests" / "test_ledger_mock_regression.py"
    if mock_path.exists():
        content = mock_path.read_text()
        is_sociable = any(
            m in content for m in ("memory://", "LedgerClient(url=", "SurrealDBLedgerAdapter(")
        )
        if not is_sociable:
            assert mock_path.name not in test_names, (
                "test_ledger_mock_regression.py uses no real-ledger marker but "
                "was classified as sociable — filter is too loose"
            )


# ── Coverage analysis ──────────────────────────────────────────────────


def test_analyze_coverage_marks_function_covered_when_body_line_executed(tmp_path):
    """Mock a coverage.json with one of the function's body lines executed;
    the analyzer must mark it covered. This is the load-bearing semantics
    of the gate — verified without running the full test suite."""
    fake_cov = {
        "files": {
            "ledger/queries.py": {
                "executed_lines": [200, 210, 220],
            }
        }
    }
    cov_json = tmp_path / "cov.json"
    cov_json.write_text(json.dumps(fake_cov))

    # Fixture: function spanning lines 190-230 — body line 200 is in the
    # executed set, so this should be marked covered.
    funcs = {"covered_fn": (190, 230), "uncovered_fn": (300, 350)}
    results = analyze_coverage(funcs, cov_json)

    by_name = {r["function"]: r for r in results}
    assert by_name["covered_fn"]["covered"] is True
    assert by_name["covered_fn"]["hits"] >= 1
    assert by_name["uncovered_fn"]["covered"] is False
    assert by_name["uncovered_fn"]["hits"] == 0


def test_analyze_coverage_raises_when_queries_file_missing(tmp_path):
    """If no sociable test even imports ledger.queries, the file won't
    appear in coverage.json. The analyzer must raise instead of
    silently returning all-uncovered (which would be a confusing failure
    mode pointing at the wrong root cause)."""
    fake_cov = {"files": {"some_other_file.py": {"executed_lines": [1, 2, 3]}}}
    cov_json = tmp_path / "cov.json"
    cov_json.write_text(json.dumps(fake_cov))

    with pytest.raises(RuntimeError, match="no entry for ledger/queries.py"):
        analyze_coverage({"fn_a": (10, 20)}, cov_json)


def test_analyze_coverage_sorts_gaps_first():
    """Markdown output should put gaps (uncovered functions) at the top
    of the table so reviewers see the failure surface immediately."""
    import tempfile

    fake_cov = {
        "files": {
            "ledger/queries.py": {"executed_lines": [100]},
        }
    }
    with tempfile.TemporaryDirectory() as tmp:
        cov_json = Path(tmp) / "cov.json"
        cov_json.write_text(json.dumps(fake_cov))
        funcs = {
            "covered_fn": (95, 105),  # line 100 is in body
            "uncovered_fn": (200, 210),
        }
        results = analyze_coverage(funcs, cov_json)

    # Gaps first (covered=False), then alphabetical
    assert results[0]["function"] == "uncovered_fn"
    assert results[1]["function"] == "covered_fn"


# ── Render ─────────────────────────────────────────────────────────────


def test_render_markdown_has_required_sections():
    """The committed snapshot is the human-readable summary; the structure
    is what reviewers see in PR diffs. Pin the sections."""
    results = [
        {
            "function": "fn_a",
            "line_start": 10,
            "line_end": 20,
            "body_size": 11,
            "hits": 5,
            "covered": True,
        }
    ]
    md = render_markdown(results, gate_mode="hard", gaps=[])

    assert "# M_surrealql_coverage (#357 sub-task 1)" in md
    assert "**Coverage**: 1/1 (100.0%)" in md
    assert "**Gate mode**: `hard`" in md
    assert "**Result**: **PASS**" in md
    assert "`fn_a`" in md
    assert "✅" in md


def test_render_markdown_reports_failure_when_gaps_exist():
    """When gaps exist, the snapshot must surface the count + render the
    gap rows distinctly — that's the diff-visibility signal reviewers
    use."""
    results = [
        {
            "function": "covered_fn",
            "line_start": 10,
            "line_end": 20,
            "body_size": 11,
            "hits": 3,
            "covered": True,
        },
        {
            "function": "gap_fn",
            "line_start": 30,
            "line_end": 40,
            "body_size": 11,
            "hits": 0,
            "covered": False,
        },
    ]
    gaps = [r for r in results if not r["covered"]]
    md = render_markdown(results, gate_mode="hard", gaps=gaps)

    assert "**FAIL** — 1 uncovered functions" in md
    assert "`gap_fn`" in md
    assert "❌" in md
    # Coverage line should show the partial number
    assert "**Coverage**: 1/2 (50.0%)" in md


# ── Snapshot consistency ───────────────────────────────────────────────


def test_committed_snapshot_lists_every_discovered_function():
    """The committed snapshot at docs/ledger-queries-coverage.md must
    enumerate every function the audit discovers. If they drift apart
    (someone added a SurrealQL function and forgot to regenerate the
    snapshot), this test fails loudly with a single-line repro hint."""
    funcs = discover_surrealql_functions()
    snapshot = (_REPO_ROOT / "docs" / "ledger-queries-coverage.md").read_text()

    missing = [fn for fn in funcs if f"`{fn}`" not in snapshot]
    assert not missing, (
        f"snapshot at docs/ledger-queries-coverage.md is stale — missing "
        f"functions: {missing}. Regenerate with: "
        f"python tests/eval_357_surrealql_coverage.py --update-snapshot"
    )
