"""Audit script for #357 sub-task 1 (Phase A).

Reads ledger/queries.py + every tests/*.py and produces a markdown table:
  function | line | issues_surrealql | referenced_in_tests | sociable_coverage

A test file is "sociable" iff it contains "memory://" — the marker for a
real SurrealDB adapter spun up via `LedgerClient(url="memory://", ...)` or
`SurrealDBLedgerAdapter` over the in-process backend, per the convention
in CLAUDE.md.

A function is "covered" iff at least one test file that references it is
sociable. Functions that issue raw SurrealQL but have no sociable
coverage are the gap rows the issue asks us to enumerate.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
QUERIES = REPO / "ledger" / "queries.py"
TESTS_DIR = REPO / "tests"
HANDLERS_DIR = REPO / "handlers"
LEDGER_DIR = REPO / "ledger"


CODE_DIRS = ("handlers", "ledger", "events", "code_locator", "adapters", "ingestion")


def find_callers_in_codebase(func_name: str) -> list[str]:
    """Return source files (excluding tests/scripts) that call func_name.

    Includes queries.py itself, since several private helpers are
    called only by other functions within queries.py — excluding
    self would falsely flag them as dead.
    """
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(func_name)}\s*\(")
    def_pattern = re.compile(rf"^\s*(async\s+)?def\s+{re.escape(func_name)}\s*\(", re.M)
    out: list[str] = []
    for dname in CODE_DIRS:
        d = REPO / dname
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            text = p.read_text()
            # Strip the function's own definition site so we don't
            # count "the def line" as a caller.
            stripped = def_pattern.sub("", text)
            if pattern.search(stripped):
                out.append(p.relative_to(REPO).as_posix())
    return out


def extract_functions(path: Path) -> list[tuple[str, int, str]]:
    """Return (name, line, body_source) for every top-level def in path."""
    source = path.read_text()
    tree = ast.parse(source)
    src_lines = source.splitlines()
    out: list[tuple[str, int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno or node.lineno
            body = "\n".join(src_lines[node.lineno - 1 : end])
            out.append((node.name, node.lineno, body))
    return out


def issues_surrealql(body: str) -> bool:
    return bool(
        re.search(r"client\.(query|execute)\s*\(", body)
        or re.search(r"await\s+client\.(query|execute)", body)
    )


def collect_test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.rglob("test_*.py"))


SOCIABLE_SIGNALS = (
    "memory://",
    "SurrealDBLedgerAdapter",
    "from adapters.ledger import",
    "adapters.ledger.get_ledger",
)
SOLITARY_SIGNALS = (
    r"\bAsyncMock\b",
    r"\bMagicMock\b",
    r"class\s+_?Fake[A-Za-z0-9_]*(Client|Adapter|Ledger)",
)


def classify_test(path: Path) -> str:
    text = path.read_text()
    sociable = any(s in text for s in SOCIABLE_SIGNALS) or "get_ledger(" in text
    solitary = any(re.search(p, text) for p in SOLITARY_SIGNALS)
    if sociable and solitary:
        return "mixed"
    if sociable:
        return "sociable"
    if solitary:
        return "solitary"
    return "neither"


def find_refs(func_name: str, test_files: list[tuple[Path, str, str]]) -> list[tuple[str, str]]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(func_name)}(?![A-Za-z0-9_])")
    out: list[tuple[str, str]] = []
    for path, text, klass in test_files:
        if pattern.search(text):
            out.append((path.relative_to(REPO).as_posix(), klass))
    return out


def main() -> int:
    funcs = extract_functions(QUERIES)
    test_files_classified = [(p, p.read_text(), classify_test(p)) for p in collect_test_files()]

    rows: list[dict] = []
    for name, line, body in funcs:
        sql = issues_surrealql(body)
        refs = find_refs(name, test_files_classified)
        sociable_refs = [r for r in refs if r[1] in ("sociable", "mixed")]
        solitary_only = bool(refs) and not sociable_refs
        callers = find_callers_in_codebase(name) if sql else []
        # Indirect sociable coverage: caller file has a sociable test file
        # that references the caller's exported name.
        indirect_sociable = False
        if sql and not sociable_refs:
            for caller_path in callers:
                caller_module = caller_path.replace("/", ".").removesuffix(".py")
                caller_basename = Path(caller_path).stem
                for tpath, ttext, tclass in test_files_classified:
                    if tclass not in ("sociable", "mixed"):
                        continue
                    if caller_module in ttext or caller_basename in ttext:
                        indirect_sociable = True
                        break
                if indirect_sociable:
                    break
        rows.append(
            {
                "name": name,
                "line": line,
                "sql": sql,
                "ref_count": len(refs),
                "sociable_count": len(sociable_refs),
                "solitary_only": solitary_only,
                "refs": refs,
                "callers": callers,
                "indirect_sociable": indirect_sociable,
            }
        )

    sql_rows = [r for r in rows if r["sql"]]
    direct = [r for r in sql_rows if r["sociable_count"] > 0]
    traps = [r for r in sql_rows if r["solitary_only"]]
    indirect = [
        r for r in sql_rows
        if r["sociable_count"] == 0 and r["indirect_sociable"] and not r["solitary_only"]
    ]
    uncovered = [
        r for r in sql_rows
        if r["sociable_count"] == 0 and not r["indirect_sociable"] and not r["solitary_only"]
    ]

    print("# Sociable test coverage audit — `ledger/queries.py`")
    print()
    print("**Issue #357 sub-task 1 — Phase A deliverable.**")
    print()
    print(f"- Total functions in `ledger/queries.py`: **{len(funcs)}**")
    print(f"- Functions issuing raw SurrealQL: **{len(sql_rows)}**")
    print()
    print("Coverage breakdown (SurrealQL-bearing functions only):")
    print()
    print(f"| Category | Count | Risk |")
    print(f"|---|---|---|")
    print(f"| **Direct sociable** (has at least one test using `memory://` or real adapter) | {len(direct)} | safe |")
    print(f"| **Solitary trap** (tests exist but ALL use `Mock`/`Fake` — #309-class) | {len(traps)} | **HIGH** |")
    print(f"| **Indirect sociable** (no direct test, but caller has sociable handler test) | {len(indirect)} | low |")
    print(f"| **Uncovered** (no direct test and no indirect coverage detected) | {len(uncovered)} | medium |")
    print()
    def category(r: dict) -> str:
        if not r["sql"]:
            return "—"
        if r["sociable_count"] > 0:
            return "direct"
        if r["solitary_only"]:
            return "**TRAP**"
        if r["indirect_sociable"]:
            return "indirect"
        return "uncovered"

    print()
    print("## Full table")
    print()
    print("| Function | Line | SQL | # refs | sociable | category | callers |")
    print("|---|---|---|---|---|---|---|")
    for r in rows:
        callers_short = ", ".join(c.split("/")[-1] for c in r["callers"][:3])
        if len(r["callers"]) > 3:
            callers_short += f" (+{len(r['callers']) - 3})"
        print(
            f"| `{r['name']}` | {r['line']} | {'yes' if r['sql'] else 'no'} | "
            f"{r['ref_count']} | {r['sociable_count']} | {category(r)} | {callers_short or '—'} |"
        )

    print()
    print("## Solitary trap rows — fix first (#309-class risk)")
    print()
    if not traps:
        print("_None._")
    else:
        for r in traps:
            ref_list = ", ".join(f"`{p}`" for p, _ in r["refs"][:5])
            more = f" (+{len(r['refs']) - 5} more)" if len(r["refs"]) > 5 else ""
            callers_str = ", ".join(r["callers"][:3]) or "—"
            print(f"- `{r['name']}` (line {r['line']})")
            print(f"  - solitary tests: {ref_list}{more}")
            print(f"  - prod callers: {callers_str}")

    print()
    print("## Uncovered rows — investigate")
    print()
    if not uncovered:
        print("_None._")
    else:
        for r in uncovered:
            callers_str = ", ".join(r["callers"][:3]) or "(no callers — possibly dead)"
            print(f"- `{r['name']}` (line {r['line']}) — callers: {callers_str}")

    print()
    print("## Indirect-only rows — low priority")
    print()
    if not indirect:
        print("_None._")
    else:
        for r in indirect:
            callers_str = ", ".join(r["callers"][:3])
            print(f"- `{r['name']}` (line {r['line']}) — exercised via: {callers_str}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
