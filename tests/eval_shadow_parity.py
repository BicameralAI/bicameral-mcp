"""M_shadow_parity — rate-based CI gate for walker ⊆ substrate (#399 Stage C).

Runs walker AND substrate over the same per-language corpus as
``tests/test_tags_extractor_parity.py`` (Python in-repo + Go from k8s/hugo
+ Rust from ripgrep/cargo), aggregates ``divergence_kind`` counts per
language, and fails the gate when ``substrate-subset`` rate exceeds 1%.

The pytest parity gate (``test_tags_extractor_parity.py``) is a binary
pass/fail per file — useful for local dev iteration. M_shadow_parity is
the rate-based CI metric: it tolerates a small percentage of failures
across a large corpus, mirroring how production-scale shadow telemetry
will surface drift once Stages D-E flip languages to shadow mode.

CLI shape mirrors the other M_* evals (M2 grounding-recall, M_skill_preflight):

    python tests/eval_shadow_parity.py --gate-mode hard -o test-results/shadow.json

Gate modes:
- ``warn`` — print metrics, exit 0 always. Baseline-discovery mode for
  first integration into CI.
- ``hard`` — exit non-zero if any language has ``substrate-subset`` rate
  above ``--max-subset-rate`` (default 0.01 = 1%, per #399 plan).

Output (stdout + optional --output JSON):
- Per-language counts of each divergence_kind
- Per-language substrate-subset rate
- Aggregate counts
- Pass/fail per gate mode

Renders a markdown summary to stdout when ``--github-summary`` is passed
(used by the workflow step that pipes to $GITHUB_STEP_SUMMARY).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import tree_sitter as ts
import tree_sitter_go as tsgo
import tree_sitter_python as tsp
import tree_sitter_rust as tsrs

# Ensure repo root is on sys.path when run as a script (so the
# code_locator + tests packages resolve).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from code_locator.indexing.symbol_extractor import (  # noqa: E402
    _WALKER_VOCAB,
    extract_symbols_from_content,
)
from code_locator.indexing.tags_extractor import (  # noqa: E402
    extract_defs_via_tags,
    load_tags_query_text,
)
from m_shadow_divergence_log import classify_divergence  # noqa: E402
from tests._oss_corpus import OssSource, discover_files, sparse_clone  # noqa: E402
from tests.test_tags_extractor_parity import (  # noqa: E402
    GO_EXCLUDE_GLOBS,
    GO_MAX_FILES_HUGO,
    GO_MAX_FILES_K8S,
    GO_SOURCE_HUGO,
    GO_SOURCE_K8S,
    PY_FIXTURE_FILES,
    RUST_EXCLUDE_GLOBS,
    RUST_MAX_FILES_CARGO,
    RUST_MAX_FILES_RIPGREP,
    RUST_SOURCE_CARGO,
    RUST_SOURCE_RIPGREP,
)

# Per-language tree-sitter parser + tags-query text. Built once per run.
_LANGUAGE_PKGS = {
    "python": ("tree_sitter_python", tsp),
    "go": ("tree_sitter_go", tsgo),
    "rust": ("tree_sitter_rust", tsrs),
}


@dataclass(frozen=True)
class LangResult:
    """Per-language aggregate result."""

    language: str
    files: int
    divergence_counts: dict[str, int]

    @property
    def substrate_subset_rate(self) -> float:
        if self.files == 0:
            return 0.0
        return self.divergence_counts.get("substrate-subset", 0) / self.files


def _setup_language(language_id: str):
    """Build (language, parser, query_text) for a target language.
    Returns None when the grammar package or its tags.scm is missing.
    """
    pkg_name, mod = _LANGUAGE_PKGS[language_id]
    try:
        lang = ts.Language(mod.language())
    except Exception:
        return None
    parser = ts.Parser(lang)
    query_text = load_tags_query_text(pkg_name)
    if query_text is None:
        return None
    return lang, parser, query_text


def _measure_file(
    language_id: str,
    rel_path: str,
    content: str,
    lang: ts.Language,
    parser: ts.Parser,
    query_text: str,
) -> str:
    """Run walker + substrate on a single file, classify divergence."""
    code_bytes = content.encode("utf-8")
    vocab = _WALKER_VOCAB.get(language_id, frozenset())

    walker_records = extract_symbols_from_content(content, language_id, rel_path)
    walker_set = {(r.name, r.type) for r in walker_records if r.type in vocab}

    tree = parser.parse(code_bytes)
    substrate_records = extract_defs_via_tags(lang, tree, code_bytes, rel_path, query_text)
    substrate_set = {(r.name, r.type) for r in substrate_records if r.type in vocab}

    return classify_divergence(walker_set, substrate_set)


def measure_python_corpus() -> LangResult:
    setup = _setup_language("python")
    if setup is None:
        return LangResult("python", 0, {})
    lang, parser, query_text = setup
    counts: Counter[str] = Counter()
    files = 0
    for rel in PY_FIXTURE_FILES:
        src = _REPO_ROOT / rel
        if not src.exists():
            continue
        content = src.read_text(encoding="utf-8")
        counts[_measure_file("python", rel, content, lang, parser, query_text)] += 1
        files += 1
    return LangResult("python", files, dict(counts))


def measure_go_corpus(cache_root: Path) -> LangResult:
    setup = _setup_language("go")
    if setup is None:
        return LangResult("go", 0, {})
    lang, parser, query_text = setup
    counts: Counter[str] = Counter()
    files = 0
    for source, max_files in (
        (GO_SOURCE_K8S, GO_MAX_FILES_K8S),
        (GO_SOURCE_HUGO, GO_MAX_FILES_HUGO),
    ):
        clone_dir = cache_root / source.cache_dirname()
        sparse_clone(source, clone_dir)
        for fp in discover_files(
            clone_dir, source, extension="go", exclude_globs=GO_EXCLUDE_GLOBS, max_files=max_files
        ):
            content = fp.read_text(encoding="utf-8")
            counts[_measure_file("go", str(fp), content, lang, parser, query_text)] += 1
            files += 1
    return LangResult("go", files, dict(counts))


def measure_rust_corpus(cache_root: Path) -> LangResult:
    setup = _setup_language("rust")
    if setup is None:
        return LangResult("rust", 0, {})
    lang, parser, query_text = setup
    counts: Counter[str] = Counter()
    files = 0
    for source, max_files in (
        (RUST_SOURCE_RIPGREP, RUST_MAX_FILES_RIPGREP),
        (RUST_SOURCE_CARGO, RUST_MAX_FILES_CARGO),
    ):
        clone_dir = cache_root / source.cache_dirname()
        sparse_clone(source, clone_dir)
        for fp in discover_files(
            clone_dir, source, extension="rs", exclude_globs=RUST_EXCLUDE_GLOBS, max_files=max_files
        ):
            content = fp.read_text(encoding="utf-8")
            counts[_measure_file("rust", str(fp), content, lang, parser, query_text)] += 1
            files += 1
    return LangResult("rust", files, dict(counts))


def _render_markdown(results: list[LangResult], passed: bool, max_subset_rate: float) -> str:
    lines = ["## M_shadow_parity (#399 Stage C)\n"]
    lines.append(f"Gate threshold: `substrate-subset rate <= {max_subset_rate:.1%}`")
    lines.append(f"Result: {'PASS ✅' if passed else 'FAIL ❌'}\n")
    lines.append(
        "| Language | Files | equal | substrate-superset | substrate-subset | symmetric | subset rate |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for r in results:
        eq = r.divergence_counts.get("equal", 0)
        sup = r.divergence_counts.get("substrate-superset", 0)
        sub = r.divergence_counts.get("substrate-subset", 0)
        sym = r.divergence_counts.get("symmetric", 0)
        rate = r.substrate_subset_rate
        lines.append(f"| {r.language} | {r.files} | {eq} | {sup} | {sub} | {sym} | {rate:.2%} |")
    return "\n".join(lines) + "\n"


def _collect_results(cache_root: Path) -> list[LangResult]:
    return [
        measure_python_corpus(),
        measure_go_corpus(cache_root),
        measure_rust_corpus(cache_root),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="M_shadow_parity — walker ⊆ substrate rate gate")
    ap.add_argument("--gate-mode", choices=("warn", "hard"), default="warn")
    ap.add_argument(
        "--max-subset-rate",
        type=float,
        default=0.01,
        help="Per-language substrate-subset rate threshold. Default 0.01 (1%%) per #399 plan.",
    )
    ap.add_argument("-o", "--output", help="Write JSON results to this path")
    ap.add_argument(
        "--github-summary",
        action="store_true",
        help="Also print markdown summary suitable for $GITHUB_STEP_SUMMARY",
    )
    ap.add_argument(
        "--cache-dir",
        help="Pre-existing dir to reuse for OSS sparse clones (CI cache). "
        "Defaults to a tmp dir scoped to this run.",
    )
    args = ap.parse_args()

    if args.cache_dir:
        cache_root = Path(args.cache_dir)
        cache_root.mkdir(parents=True, exist_ok=True)
    else:
        import tempfile

        cache_root = Path(tempfile.mkdtemp(prefix="m_shadow_parity_"))

    results = _collect_results(cache_root)

    breaches: list[str] = []
    aggregate_counts: dict[str, int] = defaultdict(int)
    for r in results:
        for k, v in r.divergence_counts.items():
            aggregate_counts[k] += v
        if r.substrate_subset_rate > args.max_subset_rate:
            breaches.append(
                f"{r.language}: substrate-subset rate "
                f"{r.substrate_subset_rate:.2%} > threshold "
                f"{args.max_subset_rate:.1%} "
                f"({r.divergence_counts.get('substrate-subset', 0)}/{r.files} files)"
            )

    passed = not breaches

    # Output routing: --github-summary prints ONLY markdown to stdout
    # (consumed by the workflow via `>> $GITHUB_STEP_SUMMARY`); regular
    # mode prints only the human-readable text (consumed by step logs).
    # Diagnostic / breach details land on stderr in both modes so
    # they're visible in the step log even when stdout is redirected.
    if args.github_summary:
        sys.stdout.write(_render_markdown(results, passed, args.max_subset_rate))
    else:
        print("=" * 64)
        print("M_shadow_parity results (#399 Stage C)")
        print("=" * 64)
        for r in results:
            sub = r.divergence_counts.get("substrate-subset", 0)
            print(
                f"  {r.language}: {r.files} files | "
                + ", ".join(f"{k}={v}" for k, v in sorted(r.divergence_counts.items()))
                + f" | subset rate {r.substrate_subset_rate:.2%}"
                + (f" ! {sub} files" if sub else "")
            )
        print()
        print(f"Aggregate: {dict(aggregate_counts)}")
        print(f"Gate threshold: substrate-subset rate <= {args.max_subset_rate:.1%}")
        print(f"Result: {'PASS' if passed else 'FAIL'}")

    if breaches:
        sys.stderr.write("\nBreaches:\n")
        for b in breaches:
            sys.stderr.write(f"  - {b}\n")

    if args.output:
        out = {
            "gate_mode": args.gate_mode,
            "max_subset_rate": args.max_subset_rate,
            "passed": passed,
            "breaches": breaches,
            "aggregate_counts": dict(aggregate_counts),
            "per_language": [
                {
                    "language": r.language,
                    "files": r.files,
                    "divergence_counts": r.divergence_counts,
                    "substrate_subset_rate": r.substrate_subset_rate,
                }
                for r in results
            ],
        }
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(out, indent=2))
        sys.stderr.write(f"\nWrote JSON: {args.output}\n")

    if args.gate_mode == "hard" and not passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
