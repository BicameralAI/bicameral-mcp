"""M_surrealql_coverage — sociable-test coverage gate for #357 sub-task 1.

Measures whether every function in ``ledger/queries.py`` that issues raw
SurrealQL has at least one body-line hit during the sociable test suite
(tests that use ``memory://`` / ``LedgerClient`` / ``SurrealDBLedgerAdapter``).

The keyword-grep first-pass audit over-reports gaps because it ignores
transitive coverage — e.g., ``_execute_idempotent_edge`` is private and
never named in a test, but it IS exercised through ``link_decision_to_subject``
and ``relate_has_identity`` which adapter tests call directly. This script
replaces that with coverage.py runtime measurement: if any line in the
function's body executes during a real-ledger test, the function is covered.

Modes:

- ``warn`` — print metrics + markdown; always exit 0. Baseline-discovery mode.
- ``hard`` — exit 1 if any SurrealQL-bearing function has zero body-line hits.

Output:

- stdout: rendered markdown table (suitable for ``>> $GITHUB_STEP_SUMMARY``)
- ``--out-markdown`` (default ``docs/ledger-queries-coverage.md``): the
  committed snapshot file. Updated in-place on every run; reviewers see
  drift in PR diffs.
- ``--output JSON``: structured artifact for downstream tooling.

CLI shape mirrors ``tests/eval_136_per_check_latency.py`` and
``tests/eval_shadow_parity.py`` — same warn→hard rollout pattern as
M_shadow_parity (precedent: #398 → #401).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_QUERIES_PATH = _REPO_ROOT / "ledger" / "queries.py"
_TESTS_DIR = _REPO_ROOT / "tests"

# Markers identifying a "sociable" test — one that exercises real SurrealDB.
# A test using only MagicMock(LedgerClient) does NOT count, even if it
# imports the right names. This filter is the load-bearing rigor lever.
_SOCIABLE_MARKERS = (
    "memory://",
    "LedgerClient(url=",
    "SurrealDBLedgerAdapter(",
)

# Pattern matching raw SurrealQL invocations in queries.py.
# Conservative: only flags ``.query(`` and ``.execute(`` on a ``client``
# binding. Won't catch SurrealQL invoked through indirection (rare here).
_SQL_CALL_RE = re.compile(r"client\.(query|execute)\(")


def discover_surrealql_functions() -> dict[str, tuple[int, int]]:
    """Return ``{function_name: (start_line, end_line)}`` for every
    function in ``ledger/queries.py`` whose body contains raw SurrealQL.

    Uses AST to get accurate line ranges (handles decorators, multi-line
    signatures, nested functions). Falls back to regex on segment text.
    """
    source = _QUERIES_PATH.read_text()
    tree = ast.parse(source)
    funcs: dict[str, tuple[int, int]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.end_lineno is None:
            continue
        body_src = ast.get_source_segment(source, node) or ""
        if _SQL_CALL_RE.search(body_src):
            funcs[node.name] = (node.lineno, node.end_lineno)

    return funcs


def discover_sociable_tests() -> list[Path]:
    """Return test files that exercise a real ledger.

    Filter: any of ``_SOCIABLE_MARKERS`` appears in the file text. False
    negatives are acceptable (a test using a real ledger but no marker
    is just unaudited — won't break the gate). False positives are NOT
    acceptable (a mock-only test counted as sociable would let bypassed
    SurrealQL ship). The markers are chosen accordingly.
    """
    tests: list[Path] = []
    for path in sorted(_TESTS_DIR.glob("test_*.py")):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if any(marker in text for marker in _SOCIABLE_MARKERS):
            tests.append(path)
    return tests


def run_coverage(tests: list[Path], cov_json_path: Path) -> bool:
    """Run pytest under coverage.py against the sociable test set.

    Returns True if a coverage JSON was produced. Tolerates non-zero
    pytest exit codes — even when some tests fail, the coverage data
    for the tests that DID run is valid and what we need.
    """
    if not tests:
        sys.stderr.write("[m357] no sociable tests discovered — refusing to run\n")
        return False

    # ``coverage run --source=ledger.queries`` scopes tracing to the one
    # file we care about. Massively cheaper than full-repo coverage.
    cov_run_cmd = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--source=ledger.queries",
        "-m",
        "pytest",
        "-q",
        "--no-header",
        # No -x: audits want full coverage data even when individual tests fail.
        # A pre-existing test failure (e.g., the elixir-mismatch baseline)
        # should not blank out coverage measurements for unrelated functions.
        "--ignore=tests/eval",  # eval scripts are not pytest-collectible
        *[str(t) for t in tests],
    ]
    # capture_output=True keeps the noise off stderr; we only need the
    # exit code (and the coverage data file as a side effect)
    run_result = subprocess.run(
        cov_run_cmd,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,  # 15 minutes — generous; expect ~2-3 minutes typical
    )

    # exit 0 = all pass; 1 = test failures (still want coverage); 2 = collection
    # error (bail); 5 = no tests selected (bail). Anything else = unexpected.
    if run_result.returncode not in (0, 1):
        sys.stderr.write(f"[m357] coverage run unexpected exit {run_result.returncode}:\n")
        sys.stderr.write(run_result.stdout[-2000:])
        sys.stderr.write(run_result.stderr[-2000:])
        return False

    # Convert .coverage SQLite DB → JSON
    cov_json_cmd = [
        sys.executable,
        "-m",
        "coverage",
        "json",
        "-o",
        str(cov_json_path),
        "--quiet",
    ]
    json_result = subprocess.run(cov_json_cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if json_result.returncode != 0:
        sys.stderr.write("[m357] coverage json failed:\n")
        sys.stderr.write(json_result.stderr[-2000:])
        return False

    return cov_json_path.exists()


def analyze_coverage(funcs: dict[str, tuple[int, int]], cov_json_path: Path) -> list[dict]:
    """For each function, check if any of its body lines executed."""
    data = json.loads(cov_json_path.read_text())
    queries_data = None
    for path_key, path_data in data.get("files", {}).items():
        if path_key.endswith("ledger/queries.py"):
            queries_data = path_data
            break
    if queries_data is None:
        raise RuntimeError(
            "coverage.json has no entry for ledger/queries.py — no sociable test touched the file"
        )
    executed = set(queries_data.get("executed_lines", []))

    results = []
    for fn_name, (start, end) in funcs.items():
        body_lines = set(range(start, end + 1))
        hits = body_lines & executed
        results.append(
            {
                "function": fn_name,
                "line_start": start,
                "line_end": end,
                "body_size": end - start + 1,
                "hits": len(hits),
                "covered": len(hits) > 0,
            }
        )
    # gaps first (so failure surface is obvious), then alphabetical
    return sorted(results, key=lambda r: (r["covered"], r["function"]))


def render_markdown(
    results: list[dict],
    gate_mode: str,
    gaps: list[dict],
) -> str:
    covered_count = sum(1 for r in results if r["covered"])
    total = len(results)
    pct = (100 * covered_count / total) if total else 0
    passed = not gaps
    pass_label = "**PASS**" if passed else f"**FAIL** — {len(gaps)} uncovered functions"

    lines = [
        "# M_surrealql_coverage (#357 sub-task 1)",
        "",
        "_Generated by `tests/eval_357_surrealql_coverage.py`. Do not hand-edit;"
        " regenerate with `python tests/eval_357_surrealql_coverage.py --update-snapshot`._",
        "",
        f"- **Coverage**: {covered_count}/{total} ({pct:.1f}%) of SurrealQL-bearing functions in `ledger/queries.py` are exercised by ≥1 sociable test.",
        f"- **Gate mode**: `{gate_mode}`. Hard mode fails CI if any function has 0 body-line hits.",
        f"- **Result**: {pass_label}",
        "",
        "## Per-function coverage",
        "",
        "| Function | Lines | Body size | Hits | Covered |",
        "|---|---:|---:|---:|:-:|",
    ]
    for r in results:
        flag = "✅" if r["covered"] else "❌"
        lines.append(
            f"| `{r['function']}` | {r['line_start']}–{r['line_end']} | "
            f"{r['body_size']} | {r['hits']} | {flag} |"
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="M_surrealql_coverage — sociable-test coverage gate (#357)"
    )
    ap.add_argument("--gate-mode", choices=("warn", "hard"), default="warn")
    ap.add_argument(
        "--out-markdown",
        default=str(_REPO_ROOT / "docs" / "ledger-queries-coverage.md"),
        help="Where to write the committed snapshot markdown.",
    )
    ap.add_argument(
        "--update-snapshot",
        action="store_true",
        help="Overwrite the snapshot file even if it already exists.",
    )
    ap.add_argument("-o", "--output", help="Write JSON results to this path.")
    args = ap.parse_args()

    funcs = discover_surrealql_functions()
    tests = discover_sociable_tests()
    sys.stderr.write(
        f"[m357] discovered {len(funcs)} SurrealQL-bearing functions, "
        f"{len(tests)} sociable test files\n"
    )

    with tempfile.TemporaryDirectory(prefix="m357_") as tmp:
        cov_json = Path(tmp) / "coverage.json"
        if not run_coverage(tests, cov_json):
            return 2
        results = analyze_coverage(funcs, cov_json)

    gaps = [r for r in results if not r["covered"]]
    markdown = render_markdown(results, args.gate_mode, gaps)

    out_md = Path(args.out_markdown)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(markdown)

    sys.stdout.write(markdown)

    if gaps:
        sys.stderr.write(f"\n[m357] {len(gaps)} gap functions:\n")
        for r in gaps:
            sys.stderr.write(f"  - {r['function']} (lines {r['line_start']}–{r['line_end']})\n")

    if args.output:
        out_json = Path(args.output)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(
            json.dumps(
                {
                    "gate_mode": args.gate_mode,
                    "total": len(results),
                    "covered": len(results) - len(gaps),
                    "gap_count": len(gaps),
                    "gaps": [r["function"] for r in gaps],
                    "results": results,
                },
                indent=2,
            )
        )
        sys.stderr.write(f"[m357] wrote JSON: {out_json}\n")

    if args.gate_mode == "hard" and gaps:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
